"""OpenAI 兼容的聊天接口客户端。

DeepSeek 官方 API、vLLM 部署的自训模型、LM Studio 都实现了同一套 /v1/chat/completions 接口，
所以一个客户端就能接所有模型，只需换 base_url 和 model。

思考过程（CoT）的字段名各家不同：DeepSeek / 旧版 vLLM 用 reasoning_content，新版 vLLM 用 reasoning。
这里统一成 LLMResponse.reasoning，Harness 不需要关心是哪家模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class LLMResponse:
    content: str | None  # 模型的可见回复（调用工具时通常为空）
    reasoning: str | None  # 思考过程，没有就是 None
    # [{"id", "type": "function", "function": {"name", "arguments"}}]
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any] = field(default_factory=dict)  # prompt_tokens / completion_tokens
    finish_reason: str | None = None


class ChatModel(Protocol):
    """Harness 只依赖这个接口。测试里用 FakeLLM 实现它，不需要真的调模型。"""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
             max_tokens: int, tool_choice: str) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", *,
                 timeout: float = 600, extra_body: dict[str, Any] | None = None) -> None:
        self.model = model
        # extra_body 用来传各家特有参数，例如 vLLM 的
        # {"chat_template_kwargs": {"enable_thinking": true}}
        self.extra_body = extra_body or {}
        self.http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
             max_tokens: int, tool_choice: str) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,  # Harness 约定每轮只调用一个工具
            "max_tokens": max_tokens,
            **self.extra_body,
        }
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

    def close(self) -> None:
        self.http.close()
