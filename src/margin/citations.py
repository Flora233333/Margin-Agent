"""引用编号：给通过校验的 cite 编号 [1] [2]……，前端的来源卡片和撰写回答（M2.5-4）用同一套编号。

规则（M2 时写在前端 citations.ts，M2.5-1 移到这里，成为唯一的一份）：
    - 只给通过校验的引用编号（cite 成功，结果里 grounded=true）；没在原文里找到的不编号；
    - 同一处原文（同一文档、同一块、同一段匹配文字）引用两次只编一个号，第二次不编号；
    - 编号按出现顺序：第一条是 1。

worker 每写一步就问一次这一步的编号（CitationNumbers.number），随 step 事件一起下发；
撰写回答时用 number_all 从全部步骤重新算一遍，结果相同。
"""

from __future__ import annotations

from typing import Any


def _key(tool_name: str, result: dict[str, Any]) -> tuple[str, str, str] | None:
    """一步是不是“通过校验的引用”；是的话返回它引的是哪一处原文。"""
    data = result.get("data") or {}
    if tool_name != "cite" or not result.get("ok") or not data.get("grounded"):
        return None
    return data["doc_id"], data["block_id"], data["matched_text"]


class CitationNumbers:
    """一次执行里已经编过号的引用。按步骤顺序调用 number()。"""

    def __init__(self) -> None:
        self.keys: list[tuple[str, str, str]] = []

    def number(self, tool_name: str, result: dict[str, Any]) -> int | None:
        key = _key(tool_name, result)
        if key is None or key in self.keys:
            return None
        self.keys.append(key)
        return len(self.keys)


def number_all(steps: list[dict[str, Any]]) -> list[int | None]:
    """按顺序给一串步骤编号（步骤是 {"tool_name", "result", ...}），返回每一步的编号。"""
    numbers = CitationNumbers()
    return [numbers.number(step["tool_name"], step["result"]) for step in steps]
