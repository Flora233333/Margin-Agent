"""工具的公共部分：上下文、错误类型、统一的返回格式，以及“执行一次工具调用”的流程。

每个工具都是一个普通函数：fn(ctx, args) -> dict。
    - ctx  ：ToolContext，里面有语料、检索器和本题的 EpisodeState
    - args ：已经通过 Pydantic 校验的参数对象
    - 返回 ：成功时的数据（dict）；失败时抛 ToolError

ToolRegistry.execute() 把它们包装成模型看到的统一格式：
    成功 {"response_version": ..., "ok": true,  "data": {...}}
    失败 {"response_version": ..., "ok": false, "error": "错误码", "detail": ..., "hint": "怎么改",
          "retryable": true, "state_changed": false}
失败不会中断 episode：错误信息作为工具结果返回给模型，让它自己修正（这就是“错误恢复”）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ...retrieval import Corpus, HybridRetriever
from ..state import EpisodeState

# 训练数据里每个工具结果都带这个字段；保留它，让自训模型看到的格式与训练时一致。
RESPONSE_VERSION = "open-tools-response-h1.2.5-v4"

# 发给模型的十个工具说明，从 RC6-C 原样导出。工具顺序也是接口的一部分。
TOOL_SCHEMAS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent.parent / "tool_schemas.json").read_text(encoding="utf-8")
)


@dataclass
class ToolContext:
    corpus: Corpus
    retriever: HybridRetriever
    state: EpisodeState


class ToolError(Exception):
    """可恢复的工具错误：返回给模型，让它换参数或换路径重试。"""

    def __init__(self, code: str, detail: Any, hint: str, *, state_changed: bool = False) -> None:
        super().__init__(str(detail))
        self.code = code
        self.detail = detail
        self.hint = hint
        self.state_changed = state_changed


ToolFn = Callable[[ToolContext, Any], dict[str, Any]]


def error_result(code: str, detail: Any, hint: str, state_changed: bool = False) -> dict[str, Any]:
    return {
        "response_version": RESPONSE_VERSION,
        "ok": False,
        "error": code,
        "detail": detail,
        "hint": hint,
        "retryable": True,
        "state_changed": state_changed,
    }


class ToolRegistry:
    """一道题的工具集合：持有本题的 ToolContext，负责校验参数、执行工具、包装结果。"""

    def __init__(self, ctx: ToolContext, tools: dict[str, tuple[ToolFn, type[BaseModel]]]) -> None:
        self.ctx = ctx
        self.tools = tools  # 工具名 -> (实现函数, 参数模型)
        self.schemas = TOOL_SCHEMAS
        self.names = [s["function"]["name"] for s in TOOL_SCHEMAS]

    @property
    def state(self) -> EpisodeState:
        return self.ctx.state

    def execute(self, name: str, raw_args: str) -> dict[str, Any]:
        """执行一次工具调用。raw_args 是模型给出的 JSON 字符串。"""
        state = self.ctx.state
        if state.terminal:
            return error_result("episode_terminated", f"episode already terminated by "
                                f"{state.terminal}", "do not issue another tool call")
        if name not in self.tools:
            return error_result("unknown_tool", name, "use one of the ten published tools")

        fn, args_model = self.tools[name]
        try:
            args = args_model.model_validate_json(raw_args)
        except ValidationError as exc:
            # include_url=False：不要把 pydantic 文档链接塞给模型；ctx 里的异常对象转成文本
            errors = exc.errors(include_url=False)
            for e in errors:
                if "ctx" in e:
                    e["ctx"] = {k: str(v) for k, v in e["ctx"].items()}
            return error_result(
                "bad_args", errors,
                "match the published tool schema and retry with corrected arguments",
            )

        # 笔记门控：写完一次笔记后，必须先有一个别的工具成功，才能再写。
        # 目的是防止模型原地反复改写笔记而不推进任务。
        if name == "write_note" and not state.note_allowed:
            return error_result(
                "write_note_requires_new_non_note_result",
                "no successful non-write_note tool result is available since the last note",
                "call a non-write_note tool first; write_note becomes available after it succeeds",
            )

        try:
            data = fn(self.ctx, args)
        except ToolError as exc:
            return error_result(exc.code, exc.detail, exc.hint, exc.state_changed)
        except (KeyError, ValueError, ArithmeticError) as exc:
            # 工具内部的参数类问题（文档不存在、表达式非法、除零……）也交给模型修正
            return error_result("invalid_request", str(exc), "correct the arguments and retry")

        state.note_allowed = name != "write_note"
        return {"response_version": RESPONSE_VERSION, "ok": True, "data": data}
