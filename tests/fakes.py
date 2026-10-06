"""假模型 FakeLLM 和构造模型回复的小工具。"""

from __future__ import annotations

import json
from typing import Any

from margin import compose, understand
from margin.llm import LLMResponse


def call(name: str, reasoning: str | None = None, **arguments: Any) -> LLMResponse:
    """构造一条“模型调用了某个工具”的回复。"""
    return LLMResponse(
        content=None,
        reasoning=reasoning,
        tool_calls=[{"id": f"c_{name}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(arguments,
                                                                        ensure_ascii=False)}}],
    )


def say(text: str) -> LLMResponse:
    """构造一条“模型没调用工具、直接说话”的回复。"""
    return LLMResponse(content=text, reasoning=None, tool_calls=[])


class FakeLLM:
    """按顺序返回预先写好的回复，并记录每次收到的请求，方便断言。

    script：chat()（Harness 每一轮）的回复。
    understand / compose：complete() 的回复，按系统提示词区分是哪一个环节——两者在 worker 里
    并行（理解题目在另一个线程），不能按调用顺序给。可以是文字，也可以是异常（模拟网关报错）；
    没准备时 complete() 抛错，调用方按“模型调用失败”处理（退回默认标题、短答案）。
    """

    def __init__(self, script: list[LLMResponse], *, understand: str | Exception | None = None,
                 compose: str | Exception | None = None) -> None:
        self.script = list(script)
        self.replies = {"understand": understand, "compose": compose}
        self.requests: list[dict[str, Any]] = []
        self.completions: list[str] = []  # complete() 被哪些环节调用过，按顺序

    def chat(self, messages, tools, max_tokens, tool_choice, on_delta=None) -> LLMResponse:
        self.requests.append({"messages": json.loads(json.dumps(messages)),
                              "tool_choice": tool_choice})
        response = self.script.pop(0)
        if on_delta and response.reasoning:
            on_delta("reasoning", response.reasoning)  # 模拟流式：整段思考作为一个片段推出
        return response

    def complete(self, messages, max_tokens, on_delta=None) -> LLMResponse:
        stage = {understand.PROMPT: "understand", compose.PROMPT: "compose"}[
            messages[0]["content"]]
        self.completions.append(stage)
        self.last_input = messages[1]["content"]  # 证据包等输入，测试可以检查
        text = self.replies[stage]
        if text is None:
            raise RuntimeError(f"FakeLLM：测试没有准备 {stage} 的回复")
        if isinstance(text, Exception):
            raise text
        if on_delta:
            on_delta("content", text)
        return LLMResponse(content=text, reasoning=None, tool_calls=[])
