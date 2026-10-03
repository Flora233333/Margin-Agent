"""RC6-C 主循环：模型思考 -> 调一个工具 -> 拿到结果 -> 再思考……直到 finalize 或触发停止条件。

RC6-C 相比普通 ReAct 循环的两个关键设计：

1. 写笔记即压缩（note compaction）
   模型成功调用 write_note 后，丢掉此前所有的工具调用和结果，上下文重置为
   [system, 题目, 最新笔记]，之后的调用接在后面。长任务的上下文因此不会无限膨胀，
   代价是模型必须把后续还需要的信息写进笔记。

2. 思考回灌（reasoning replay）
   每轮 assistant 消息带上 reasoning_content，下一轮请求时模型能看到自己之前的思考。

停止条件（trace.violation 的取值）：
    max_turns                       非 write_note 的工具调用次数用完
    provider_call_limit             模型调用总次数用完（含写笔记）
    provider_protocol_retry_exhausted  模型两次不调用工具
    multiple_tool_calls             一轮调用了多个工具（约定每轮只能一个）
    answer_format_retry_exhausted   finalize 答案格式连续错误 6 次
    duplicate_no_progress           连续 3 次相同调用得到相同结果（原地打转）
    repeated_tool_error             同一个调用以同一个错误失败 3 次
正常结束时 violation 为 None，trace.final 是提交的答案。
"""

from __future__ import annotations

import copy
import functools
import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..llm import ChatModel
from .prompt import PROTOCOL_REPAIR_MESSAGE, initial_messages, note_message
from .tools import ToolRegistry

MAX_FORMAT_RETRIES = 6


@dataclass
class Step:
    """一轮的完整记录。即使上下文被压缩，这里也保留全部历史，供前端展示和训练导出。"""

    turn: int
    reasoning: str | None
    tool_name: str
    arguments: str  # 模型给出的原始 JSON 字符串
    result: dict[str, Any]
    llm_seconds: float = 0.0  # 这一轮模型调用的耗时（不含工具执行），用来比较模型速度


@dataclass
class Trace:
    task: dict[str, Any]
    messages: list[dict[str, Any]]  # 当前发给模型的上下文（会被压缩）
    steps: list[Step] = field(default_factory=list)
    usage: list[dict[str, Any]] = field(default_factory=list)  # 每次模型调用的 token 用量
    final: dict[str, Any] | None = None
    violation: str | None = None


def run_episode(
    task: dict[str, Any],
    llm: ChatModel,
    registry: ToolRegistry,
    *,
    max_turns: int = 30,
    max_note_updates: int = 20,
    max_tokens: int = 8192,
    on_delta: Callable[[int, str, str], None] | None = None,
) -> Trace:
    initial = initial_messages(task)
    trace = Trace(task=task, messages=copy.deepcopy(initial))

    tool_choice = "auto"
    non_note_turns = 0
    no_tool_rounds = 0
    format_errors = 0
    last_key: str | None = None  # 上一次“调用 + 结果”的指纹，用于发现原地打转
    same_key_count = 0
    error_counts: Counter[str] = Counter()

    # 总调用上限 = 普通工具轮数 + 写笔记次数
    for turn in range(max_turns + max_note_updates):
        if non_note_turns >= max_turns:
            trace.violation = "max_turns"
            break

        # 模型调用失败（网络、限流、5xx）直接抛出，由上层决定重试，这里不吞异常。
        started = time.monotonic()
        # 传了 on_delta 就走流式：思考片段一到就回调 on_delta(轮次, "reasoning", 片段)
        stream = functools.partial(on_delta, turn) if on_delta else None
        response = llm.chat(trace.messages, registry.schemas, max_tokens, tool_choice, stream)
        llm_seconds = time.monotonic() - started
        trace.usage.append(response.usage)

        # 只保留 id / type / function 三个标准字段，去掉各家接口附带的额外字段（如 index）
        calls = [
            {"id": c.get("id") or f"call_{turn}", "type": "function",
             "function": {"name": c["function"]["name"],
                          "arguments": c["function"].get("arguments") or "{}"}}
            for c in response.tool_calls
        ]
        assistant = {"role": "assistant", "content": response.content, "tool_calls": calls[:1]}
        if response.reasoning:
            assistant["reasoning_content"] = response.reasoning  # 思考回灌
        trace.messages.append(assistant)

        # ---- 协议检查：每轮必须恰好调用一个工具 ----
        if not calls:
            no_tool_rounds += 1
            if no_tool_rounds >= 2:
                trace.violation = "provider_protocol_retry_exhausted"
                break
            trace.messages.append({"role": "user", "content": PROTOCOL_REPAIR_MESSAGE})
            tool_choice = "required"  # 下一轮强制调用工具
            continue
        if len(calls) > 1:
            trace.violation = "multiple_tool_calls"
            break
        tool_choice = "auto"

        # ---- 执行工具 ----
        call = calls[0]
        name = call["function"]["name"]
        raw_args = call["function"]["arguments"]
        result = registry.execute(name, raw_args)
        trace.steps.append(Step(turn, response.reasoning, name, raw_args, result, llm_seconds))
        if name != "write_note":
            non_note_turns += 1

        if name == "write_note" and result["ok"]:
            # 压缩点：历史全部丢弃，只保留题目和最新笔记
            note = note_message(registry.state.note_revision, registry.state.note)
            trace.messages = copy.deepcopy(initial) + [note]
        else:
            trace.messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            })

        # ---- 停止条件 ----
        if name == "finalize" and result.get("error") in {"answer_format_invalid", "bad_args"}:
            # 答案格式错误是正常的修正过程，单独计数，不算“原地打转”
            format_errors += 1
            last_key, same_key_count = None, 0
            if format_errors >= MAX_FORMAT_RETRIES:
                trace.violation = "answer_format_retry_exhausted"
                break
            continue

        call_key = name + _canonical_args(raw_args)
        result_key = call_key + json.dumps(result, ensure_ascii=False, sort_keys=True)
        same_key_count = same_key_count + 1 if result_key == last_key else 1
        last_key = result_key
        if same_key_count >= 3:
            trace.violation = "duplicate_no_progress"
            break
        if not result["ok"]:
            error_counts[result["error"] + call_key] += 1
            if error_counts[result["error"] + call_key] >= 3:
                trace.violation = "repeated_tool_error"
                break

        if name in {"finalize", "escalate"} and result["ok"]:
            trace.final = {"name": name, **result["data"]}
            break
    else:
        trace.violation = "provider_call_limit"
    return trace


def _canonical_args(raw_args: str) -> str:
    """参数 JSON 规范化（键排序），让 {"a":1,"b":2} 和 {"b":2,"a":1} 视为同一个调用。"""
    try:
        return json.dumps(json.loads(raw_args), ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        return raw_args
