"""假模型 FakeLLM 和构造模型回复的小工具。"""

from __future__ import annotations

import json
from typing import Any

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
    """按顺序返回预先写好的回复，并记录每次收到的请求，方便断言。"""

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages, tools, max_tokens, tool_choice, on_delta=None) -> LLMResponse:
        self.requests.append({"messages": json.loads(json.dumps(messages)),
                              "tool_choice": tool_choice})
        response = self.script.pop(0)
        if on_delta and response.reasoning:
            on_delta("reasoning", response.reasoning)  # 模拟流式：整段思考作为一个片段推出
        return response
