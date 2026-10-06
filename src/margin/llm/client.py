"""OpenAI 兼容的聊天接口客户端。

DeepSeek 官方 API、vLLM 部署的自训模型、LM Studio 都实现了同一套 /v1/chat/completions 接口，
所以一个客户端就能接所有模型，只需换 base_url 和 model。

思考过程（CoT）的字段名各家不同：DeepSeek / 旧版 vLLM 用 reasoning_content，新版 vLLM 用 reasoning。
这里统一成 LLMResponse.reasoning，Harness 不需要关心是哪家模型。

两个入口：
    chat()      Harness 用，带十个工具的说明，模型每轮调用一个工具；
    complete()  不带工具，模型直接写文字：理解题目、撰写回答（M2.5，同一个模型、独立的提示词）。

两种调用方式，返回的 LLMResponse 完全一样：
    非流式：等模型全部生成完，一次拿到整条回复。
    流式（传 on_delta）：服务端用 SSE 一小块一小块地推，每来一块思考 / 正文就回调一次，
    前端因此能“逐字”显示思考过程；工具调用的参数也是分块来的，在这里拼完整再返回。

超时（D18）：模型网关偶尔会“挂住”不返回，没有超时的话 worker 会一直等下去。
    连接     10 秒连不上就放弃
    等数据   60 秒收不到任何数据就放弃（流式时对首块和块间都生效）
    总时长   180 秒（实测单轮最长 120 秒，留了余量）；流式时每收到一块检查一次
    httpx 的读超时只有一个值，区分不了“首块”和“块间”，所以块间也是 60 秒，而不是方案里的 30 秒；
    更细的卡死判定由 M4 的监督者负责。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# 流式回调：on_delta(kind, text)，kind 是 "reasoning"（思考）或 "content"（正文）
OnDelta = Callable[[str, str], None]

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
TOTAL_TIMEOUT = 180


class LLMTimeout(Exception):
    """一次模型调用超过了总时长上限。"""


@dataclass
class LLMResponse:
    content: str | None  # 模型的可见回复（调用工具时通常为空）
    reasoning: str | None  # 思考过程，没有就是 None
    # [{"id", "type": "function", "function": {"name", "arguments"}}]
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any] = field(default_factory=dict)  # prompt_tokens / completion_tokens
    finish_reason: str | None = None


class ChatModel(Protocol):
    """Harness 和 worker 只依赖这个接口。测试里用 FakeLLM 实现它，不需要真的调模型。"""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
             max_tokens: int, tool_choice: str,
             on_delta: OnDelta | None = None) -> LLMResponse: ...

    def complete(self, messages: list[dict[str, Any]], max_tokens: int,
                 on_delta: OnDelta | None = None) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", *,
                 total_timeout: float = TOTAL_TIMEOUT,
                 extra_body: dict[str, Any] | None = None) -> None:
        self.model = model
        self.total_timeout = total_timeout
        # extra_body 用来传各家特有参数，例如 vLLM 的
        # {"chat_template_kwargs": {"enable_thinking": true}}
        self.extra_body = extra_body or {}
        self.http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            # 非流式：整条回复生成完才开始返回数据，所以“等数据”的上限就是总时长
            timeout=httpx.Timeout(total_timeout, connect=CONNECT_TIMEOUT),
        )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
             max_tokens: int, tool_choice: str,
             on_delta: OnDelta | None = None) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,  # Harness 约定每轮只调用一个工具
            "max_tokens": max_tokens,
            **self.extra_body,
        }
        return self._send(body, on_delta)

    def complete(self, messages: list[dict[str, Any]], max_tokens: int,
                 on_delta: OnDelta | None = None) -> LLMResponse:
        """不带工具的调用：请求里没有 tools，模型的回复就是 content 里的文字。"""
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                **self.extra_body}
        return self._send(body, on_delta)

    def _send(self, body: dict[str, Any], on_delta: OnDelta | None) -> LLMResponse:
        if on_delta is not None:
            return self._chat_stream(body, on_delta)

        response = self.http.post("/chat/completions", json=body)
        response.raise_for_status()  # 4xx/5xx 直接抛异常，由上层决定是否重试
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        return LLMResponse(
            content=message.get("content"),
            reasoning=message.get("reasoning_content") or message.get("reasoning"),
            tool_calls=message.get("tool_calls") or [],
            usage=data.get("usage") or {},
            finish_reason=choice.get("finish_reason"),
        )

    def _chat_stream(self, body: dict[str, Any], on_delta: OnDelta) -> LLMResponse:
        """SSE 流式调用。每行形如 `data: {chunk}`，最后一行是 `data: [DONE]`。

        一个 chunk 的 delta 里可能有：思考片段、正文片段、工具调用片段。
        工具调用第一块带 id 和函数名，之后几块只带 index 和一段 arguments，按 index 拼起来。
        include_usage=true 时，服务端在最后额外发一个 choices 为空、只带 usage 的 chunk。
        """
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        reasoning: list[str] = []
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason = None
        deadline = time.monotonic() + self.total_timeout

        timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
        with self.http.stream("POST", "/chat/completions", json=body, timeout=timeout) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() > deadline:  # 一直有数据、但总时长超了（例如模型反复输出）
                    raise LLMTimeout(f"模型调用超过 {self.total_timeout} 秒")
                if not line.startswith("data:"):
                    continue  # 空行、SSE 注释行（心跳）
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                usage = chunk.get("usage") or usage
                if not chunk.get("choices"):
                    continue
                choice = chunk["choices"][0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}

                think = delta.get("reasoning_content") or delta.get("reasoning")
                if think:
                    reasoning.append(think)
                    on_delta("reasoning", think)
                if delta.get("content"):
                    content.append(delta["content"])
                    on_delta("content", delta["content"])
                for piece in delta.get("tool_calls") or []:
                    call = calls.setdefault(piece["index"], {
                        "id": None, "type": "function",
                        "function": {"name": "", "arguments": ""}})
                    call["id"] = piece.get("id") or call["id"]
                    function = piece.get("function") or {}
                    call["function"]["name"] += function.get("name") or ""
                    call["function"]["arguments"] += function.get("arguments") or ""

        return LLMResponse(
            content="".join(content) or None,
            reasoning="".join(reasoning) or None,
            tool_calls=[calls[i] for i in sorted(calls)],
            usage=usage,
            finish_reason=finish_reason,
        )

    def close(self) -> None:
        self.http.close()


def describe_error(exc: Exception) -> str:
    """写进数据库、会展示给用户的错误说明。不用 str(exc)：httpx 的报错里带网关地址。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"llm_http_{exc.response.status_code}"
    return type(exc).__name__
