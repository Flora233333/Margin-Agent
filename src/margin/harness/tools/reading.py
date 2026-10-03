"""阅读类工具：read_section（按行读原文）、find_in_block（块内找关键词）、cite（记录引用）。

三者共同的规矩：只能操作搜索结果里出现过的 (doc_id, block_id)。
读到的原文会记进 state.visible_texts，cite 只接受“看到过”的原文。
"""

from __future__ import annotations

import unicodedata
from typing import Any

from ..citation import match_citation
from .args import CiteArgs, FindInBlockArgs, ReadSectionArgs
from .base import ToolContext, ToolError
from .search import scope_of

MAX_FIND_CHARS = 5000  # find_in_block 一次最多返回的原文字符数


def _located_block(ctx: ToolContext, doc_id: str, block_id: str) -> dict[str, Any]:
    """检查 (doc_id, block_id) 已被搜索定位过，并返回 block。"""
    if not ctx.state.searched:
        raise ToolError("search_required", "no successful search_docs call",
                        "call search_docs first")
    if (doc_id, block_id) not in ctx.state.located_pairs:
        raise ToolError("block_not_located",
                        f"{doc_id}/{block_id} was not returned by a successful search",
                        "use a doc/block pair from search_docs or search_in_document")
    return ctx.corpus.by_id[block_id]


def read_section(ctx: ToolContext, args: ReadSectionArgs) -> dict[str, Any]:
    """从第 row_offset 行开始读原文，最多 max_chars 个字符；返回 next_row_offset 方便续读。"""
    block = _located_block(ctx, args.doc_id, args.block_id)
    text = str(block["text"])
    lines = text.splitlines() or [""]
    if args.row_offset >= len(lines):
        raise ToolError("offset_out_of_range",
                        f"row_offset={args.row_offset}, block_lines={len(lines)}",
                        f"use row_offset between 0 and {len(lines) - 1}")

    # 按整行累加，直到超过字符上限；第一行就超长时截断这一行
    selected: list[str] = []
    used = 0
    truncated = False
    for line in lines[args.row_offset:]:
        cost = len(line) + (1 if selected else 0)  # 行间的换行符也算 1 个字符
        if selected and used + cost > args.max_chars:
            break
        if not selected and cost > args.max_chars:
            selected.append(line[: args.max_chars])
            truncated = True
            break
        selected.append(line)
        used += cost

    returned = "\n".join(selected)
    end = args.row_offset + len(selected)
    next_offset = end if end < len(lines) else None
    ctx.state.visible_texts.setdefault((args.doc_id, args.block_id), []).append(returned)
    scope = scope_of(block)
    return {
        "doc_id": args.doc_id,
        "block_id": args.block_id,
        "section_id": block.get("section_id"),
        "has_table": bool(block.get("has_table")),
        "scope": scope,
        "scope_ambiguity": scope["ambiguous"],
        "block_chars": len(text),
        "block_lines": len(lines),
        "row_offset": args.row_offset,
        "returned_line_end_exclusive": end,
        "remaining_lines": len(lines) - end,
        "next_row_offset": next_offset,
        "complete": next_offset is None and not truncated,
        "text": returned,
    }


def _keyword_spans(text: str, keyword: str) -> list[tuple[int, int]]:
    """在原文里找关键词所有出现位置（忽略全半角和大小写），返回原文中的 (start, end)。"""
    chars, positions = [], []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).lower():
            chars.append(c)
            positions.append(i)
    haystack = "".join(chars)
    needle = unicodedata.normalize("NFKC", keyword).lower()
    spans, start = [], haystack.find(needle)
    while start >= 0:
        spans.append((positions[start], positions[start + len(needle) - 1] + 1))
        start = haystack.find(needle, start + 1)
    return spans


def find_in_block(ctx: ToolContext, args: FindInBlockArgs) -> dict[str, Any]:
    """在长 block 里找关键词，返回命中处前后一段原文；相邻命中合并成一段。"""
    block = _located_block(ctx, args.doc_id, args.block_id)
    text = str(block["text"])

    # 1. 每个命中扩展成 [命中前 before_chars, 命中后 after_chars] 的窗口
    hits = sorted(
        (max(0, s - args.before_chars), min(len(text), e + args.after_chars), s, e, kw)
        for kw in args.keywords
        for s, e in _keyword_spans(text, kw)
    )
    # 2. 重叠的窗口合并
    merged: list[dict[str, Any]] = []
    for ctx_start, ctx_end, s, e, kw in hits:
        if merged and ctx_start <= merged[-1]["context_end"]:
            m = merged[-1]
            m["context_end"] = max(m["context_end"], ctx_end)
            m["start"], m["end"] = min(m["start"], s), max(m["end"], e)
            m["keywords"].add(kw)
        else:
            merged.append({"context_start": ctx_start, "context_end": ctx_end,
                           "start": s, "end": e, "keywords": {kw}})
    # 3. match_mode=all 时，只保留包含全部关键词的窗口
    if args.match_mode == "all":
        merged = [m for m in merged if set(args.keywords) <= m["keywords"]]

    # 4. 按总字数上限截取返回
    matches = []
    budget = MAX_FIND_CHARS
    for m in merged[: args.max_matches]:
        if budget < m["end"] - m["start"]:
            break
        ctx_start = m["context_start"]
        ctx_end = min(m["context_end"], ctx_start + budget)
        if ctx_end < m["end"]:  # 窗口被截短时，保证命中本身完整
            ctx_end, ctx_start = m["end"], max(m["context_start"], m["end"] - budget)
        snippet = text[ctx_start:ctx_end]
        keywords = sorted(m["keywords"], key=args.keywords.index)
        matches.append({"keyword": keywords[0], "keywords": keywords, "start": m["start"],
                        "end": m["end"], "context_start": ctx_start, "context_end": ctx_end,
                        "text": snippet})
        budget -= len(snippet)
        ctx.state.visible_texts.setdefault((args.doc_id, args.block_id), []).append(snippet)

    scope = scope_of(block)
    return {
        "doc_id": args.doc_id,
        "block_id": args.block_id,
        "block_chars": len(text),
        "has_table": bool(block.get("has_table")),
        "scope": scope,
        "scope_ambiguity": scope["ambiguous"],
        "keywords": args.keywords,
        "match_count_total": len(hits) if args.match_mode == "any" else len(merged),
        "matches_returned": len(matches),
        "truncated": len(merged) > len(matches),
        "matches": matches,
    }


def cite(ctx: ToolContext, args: CiteArgs) -> dict[str, Any]:
    """记录一条支撑答案的原文引用；quote 必须逐字（或仅空白/标点差异）来自看过的原文。"""
    block = _located_block(ctx, args.doc_id, args.block_id)
    visible = ctx.state.visible_texts.get((args.doc_id, args.block_id), [])
    match = match_citation(str(block["text"]), visible, args.quote)
    if not match.accepted:
        raise ToolError(
            match.error or "quote_not_found",
            {"raw_quote": args.quote, "suggested_quote": match.suggested_quote},
            "resubmit the exact suggested canonical quote after reading its source span",
        )
    citation = {
        "doc_id": args.doc_id,
        "block_id": args.block_id,
        "quote": args.quote,
        "matched_text": match.matched_text,
        "grounded": True,
        "level": match.level,
        "start": match.start,
        "end": match.end,
    }
    ctx.state.citations.append(citation)
    return citation
