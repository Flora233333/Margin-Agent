"""初始消息：[system 提示词, 题目]。

system 提示词原样复制自 RC6-C（prompts/system_rc6c.txt），不要改动措辞：
自训模型是在这份提示词下训练的，改一个字都可能改变它的行为。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_rc6c.txt").read_text(encoding="utf-8")


def render_question(task: dict[str, Any]) -> str:
    """题目 + 选项。选项可以是 {"A": "...", ...} 或 ["...", "..."]。"""
    parts = [str(task["question"]).strip()]
    options = task.get("options")
    if isinstance(options, dict) and options:
        parts += ["选项：", "\n".join(f"{k}. {options[k]}" for k in sorted(options))]
    elif isinstance(options, list) and options:
        parts += ["选项：", "\n".join(f"{chr(65 + i)}. {v}" for i, v in enumerate(options))]
    return "\n".join(parts)


def initial_messages(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_question(task)},
    ]


def note_message(revision: int, note: str) -> dict[str, Any]:
    """压缩后代替全部历史的那条消息：只剩最新的工作笔记。"""
    return {"role": "user", "content": f"工作笔记 revision={revision}：\n{note}"}


# 模型这一轮没有调用工具时，追加这条提醒，并要求下一轮必须调用工具。
PROTOCOL_REPAIR_MESSAGE = (
    "本轮未调用工具。每轮都必须使用一个工具执行行动；如需提交答案，请调用 finalize 工具，"
    "不要直接输出答案文本。"
)
