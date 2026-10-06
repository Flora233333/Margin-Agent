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
    """按顺序返回预先写好的回复，并记录每次收到的请求，方便断言。

    script：chat()（Harness 每一轮）的回复；texts：complete()（理解题目、撰写回答）的回复，
    可以是文字，也可以是异常（模拟网关报错）。没准备 texts 时 complete() 抛错，
    调用方按“模型调用失败”处理（理解题目退回默认标题）。
    """

    def __init__(self, script: list[LLMResponse],
                 texts: list[str | Exception] | None = None) -> None:
        self.script = list(script)
        self.texts = list(texts or [])
        self.requests: list[dict[str, Any]] = []
        self.completions: list[list[dict[str, Any]]] = []  # complete() 收到的 messages

    def chat(self, messages, tools, max_tokens, tool_choice, on_delta=None) -> LLMResponse:
        self.requests.append({"messages": json.loads(json.dumps(messages)),
                              "tool_choice": tool_choice})
        response = self.script.pop(0)
        if on_delta and response.reasoning:
            on_delta("reasoning", response.reasoning)  # 模拟流式：整段思考作为一个片段推出
        return response

    def complete(self, messages, max_tokens, on_delta=None) -> LLMResponse:
        self.completions.append(json.loads(json.dumps(messages)))
        if not self.texts:
            raise RuntimeError("FakeLLM：测试没有准备 complete() 的回复")
        text = self.texts.pop(0)
        if isinstance(text, Exception):
            raise text
        if on_delta:
            on_delta("content", text)
        return LLMResponse(content=text, reasoning=None, tool_calls=[])
