"""其余工具：compute、date_calc（计算），write_note（笔记），finalize、escalate（结束）。"""

from __future__ import annotations

from typing import Any

from ..answers import normalize_answers
from ..calc import calculate_date
from ..calc import compute as run_compute
from .args import ComputeArgs, DateCalcArgs, EscalateArgs, FinalizeArgs, WriteNoteArgs
from .base import ToolContext, ToolError


def compute(ctx: ToolContext, args: ComputeArgs) -> dict[str, Any]:
    """确定性四则运算 / 乘方 / 常用函数；表达式非法时返回 compute_invalid 让模型改写。"""
    try:
        result = run_compute(args.expression, args.variables, args.precision)
    except (SyntaxError, ValueError, ArithmeticError) as exc:
        raise ToolError(
            "compute_invalid", str(exc),
            "use numbers, variables, parentheses, + - * / **, × ÷, numeric percent suffixes, "
            "and max, min, abs, round, sum, avg, sqrt, ln, log10, exp",
        ) from exc
    return {"expression_raw": args.expression, "variables_raw": args.variables, **result,
            "precision": args.precision}


def date_calc(ctx: ToolContext, args: DateCalcArgs) -> dict[str, Any]:
    try:
        return calculate_date(args.base_date, args.count_from, args.duration, args.unit,
                              args.calendar, args.roll)
    except (ValueError, OverflowError) as exc:
        raise ToolError("date_calc_invalid", str(exc),
                        "correct the date arguments and retry") from exc


def write_note(ctx: ToolContext, args: WriteNoteArgs) -> dict[str, Any]:
    """覆盖保存工作笔记。真正的“压缩上下文”发生在 loop.py：成功写笔记后清空历史。"""
    ctx.state.note = args.note
    ctx.state.note_revision += 1
    return {"revision": ctx.state.note_revision, "note": args.note}


def finalize(ctx: ToolContext, args: FinalizeArgs) -> dict[str, Any]:
    """提交最终答案：按题目的 answer_format 规范化，格式有歧义时返回错误让模型重交。"""
    state = ctx.state
    if not state.searched:
        raise ToolError("search_required",
                        "finalize requires at least one successful search_docs call",
                        "call search_docs before finalizing")
    answer_format = str(state.task.get("answer_format") or "text")
    try:
        normalized = normalize_answers(args.answers, answer_format)
    except ValueError as exc:
        raise ToolError(
            "answer_format_invalid", str(exc),
            "submit one to four answer values; use A/B or 正确/错误 for tf, "
            "and separate numeric components with semicolons or newlines",
        ) from exc
    state.final_answers = normalized
    state.terminal = "finalize"
    return {"submitted": normalized, "raw": args.answers}


def escalate(ctx: ToolContext, args: EscalateArgs) -> dict[str, Any]:
    """证据确实不足 / 互相冲突 / 超出范围时放弃作答（诊断用的终止）。"""
    if not ctx.state.searched:
        raise ToolError("search_required",
                        "escalate requires at least one successful search_docs call",
                        "try a document search before escalating")
    ctx.state.terminal = "escalate"
    return {"accepted": False, "reason_code": args.reason_code, "detail": args.detail}
