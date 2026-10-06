"""把一次执行的步骤整理成“已校验的证据包”，以及检查一段回答（[n] 是否合法、数字是否有出处）。

E2（撰写回答）和 E5（一次交齐）共用。正式实现（M2.5-1、M2.5-4）会把这里的规则搬进后端：
    - 引用编号：只取通过校验的 cite，同一处原文只编一个号，按出现顺序（和前端 citations.ts 相同）；
    - 数字核对：回答里的数字必须出现在问题、引用原文（含前后文）、计算结果或提交的短答案里。
      工作笔记是模型自己写的，不算出处。
"""

from __future__ import annotations

import json
import re
from typing import Any

CONTEXT_CHARS = 60


def normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """两种来源的步骤统一成 {tool, arguments(dict), result, reasoning}。

    来源：实验脚本存的 JSON（键名 tool），或 API 返回的 run（键名 tool_name）。
    """
    rows = []
    for s in steps:
        raw = s.get("arguments") or "{}"
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            arguments = {}
        rows.append({"tool": s.get("tool") or s.get("tool_name"), "arguments": arguments,
                     "result": s.get("result") or {},
                     "reasoning": s.get("reasoning") or ""})
    return rows


def _read_texts(steps: list[dict[str, Any]], doc_id: str, block_id: str) -> list[str]:
    texts = []
    for s in steps:
        data = s["result"].get("data") or {}
        if data.get("doc_id") != doc_id or data.get("block_id") != block_id:
            continue
        if s["tool"] == "read_section":
            texts.append(data.get("text") or "")
        elif s["tool"] == "find_in_block":
            texts += [m.get("text") or "" for m in data.get("matches") or []]
    return texts


def citations_of(steps: list[dict[str, Any]], dedupe: bool = True) -> list[dict[str, Any]]:
    """dedupe=False：每次成功的 cite 都编号（E5 里模型按 cite 返回的 citation_no 写 [n]）。"""
    citations: list[dict[str, Any]] = []
    for s in steps:
        data = s["result"].get("data") or {}
        if s["tool"] != "cite" or not data.get("grounded"):
            continue
        match = data["matched_text"]
        key = (data["doc_id"], data["block_id"], match)
        if dedupe and any((c["doc_id"], c["block_id"], c["match"]) == key for c in citations):
            continue
        before = after = ""
        for text in _read_texts(steps, data["doc_id"], data["block_id"]):
            at = text.find(match)
            if at >= 0:
                before = text[max(0, at - CONTEXT_CHARS):at]
                after = text[at + len(match):at + len(match) + CONTEXT_CHARS]
                break
        citations.append({"no": len(citations) + 1, "doc_id": data["doc_id"],
                          "block_id": data["block_id"], "match": match,
                          "before": before, "after": after})
    return citations


def computations_of(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for s in steps:
        data = s["result"].get("data") or {}
        if s["tool"] in ("compute", "date_calc") and s["result"].get("ok"):
            a = s["arguments"]
            if s["tool"] == "compute":
                rows.append({"expression": a.get("expression", ""),
                             "exact": str(data.get("result_exact", "")),
                             "rounded": str(data.get("result", ""))})
            else:
                expression = f"{a.get('base_date')} 起 {a.get('duration')} {a.get('unit')}"
                date = str(data.get("date", ""))
                rows.append({"expression": expression, "exact": date, "rounded": date})
    return rows


def latest_note(steps: list[dict[str, Any]]) -> str:
    notes = [s["result"]["data"]["note"] for s in steps
             if s["tool"] == "write_note" and s["result"].get("ok") and s["result"].get("data")]
    return notes[-1] if notes else ""


def package(question: str, answer_format: str, final: dict[str, Any] | None,
            steps: list[dict[str, Any]], with_reasoning: bool = False,
            dedupe: bool = True) -> dict[str, Any]:
    """撰写回答的输入。with_reasoning=True 时另加每一步的完整思考（E2 的输入 b）。"""
    pkg = {
        "question": question,
        "answer_format": answer_format,
        "final": final or {},
        "citations": citations_of(steps, dedupe),
        "computations": computations_of(steps),
        "note": latest_note(steps),
    }
    if with_reasoning:
        pkg["reasoning"] = [s["reasoning"] for s in steps if s["reasoning"]]
    return pkg


NUMBER = re.compile(r"\d[\d,，]*(?:\.\d+)?")
CITE = re.compile(r"\[(\d+)\]")


def _plain(number: str) -> str:
    return number.replace(",", "").replace("，", "")


def haystack(pkg: dict[str, Any]) -> str:
    """数字核对的“出处”：问题、引用原文和前后文、计算、提交的短答案。去掉千分位逗号，方便比较。"""
    final = pkg["final"]
    parts = [pkg["question"], *(final.get("submitted") or []), *(final.get("raw") or [])]
    for c in pkg["citations"]:
        parts += [c["before"], c["match"], c["after"]]
    for row in pkg["computations"]:
        parts += [row["expression"], row["exact"], row["rounded"]]
    return _plain(" ".join(parts))


def check(answer: str, pkg: dict[str, Any]) -> dict[str, Any]:
    """检查一段带 [n] 的回答：删掉不存在的编号；找出没有出处的数字（标记，不删）。"""
    total = len(pkg["citations"])
    invalid = sorted({int(n) for n in CITE.findall(answer) if not 1 <= int(n) <= total})
    cleaned = CITE.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= total else "", answer)
    used = sorted({int(n) for n in CITE.findall(cleaned)})
    hay = haystack(pkg)
    numbers = [_plain(n) for n in NUMBER.findall(CITE.sub("", cleaned))]
    unverified = sorted({n for n in numbers if n not in hay})
    return {"text": cleaned, "invalid_refs": invalid, "used_refs": used, "total_refs": total,
            "numbers": len(numbers), "unverified": unverified}
