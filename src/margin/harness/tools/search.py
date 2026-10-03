"""检索类工具：search_docs（全库找文档）、search_in_document（文档内找 block）。"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .args import SearchDocsArgs, SearchInDocumentArgs
from .base import ToolContext, ToolError

# 口径信号：金融数据最常见的坑是“母公司 vs 合并”口径混用，提前在结果里标出来提醒模型。
_SCOPE_PATTERNS = [
    (r"母公司", "parent_company", "母公司"),
    (r"合并", "consolidated", "合并"),
    (r"本集团|集团口径", "group", "本集团"),
    (r"本行", "reporting_bank", "本行"),
    (r"本公司|公司口径", "reporting_company", "本公司"),
]


def preview(text: str, limit: int = 200) -> str:
    """压缩空白后截取前 limit 个字符，作为结果预览。"""
    return re.sub(r"\s+", " ", text).strip()[:limit]


def scope_of(block: dict[str, Any]) -> dict[str, Any]:
    """从章节路径和正文开头判断这个 block 的数据口径。"""
    text = " ".join([*map(str, block.get("section_path") or []), str(block["text"])[:800]])
    signals = sorted({value for pattern, value, _ in _SCOPE_PATTERNS if re.search(pattern, text)})
    if len(signals) == 1:
        value = signals[0]
        label = next(lbl for _, v, lbl in _SCOPE_PATTERNS if v == value)
    elif signals:
        value, label = "mixed", "混合口径"
    else:
        value, label = "unknown", "口径不明"
    return {
        "value": value,
        "label": label,
        "signals": signals,
        # 表格且口径不确定：模型需要回原文确认
        "ambiguous": bool(block.get("has_table")) and value in {"mixed", "unknown"},
    }


def block_summary(block: dict[str, Any]) -> dict[str, Any]:
    """搜索结果里每个 block 附带的元信息：多长、有没有表、在哪个章节、口径、预览。"""
    text = str(block["text"])
    scope = scope_of(block)
    return {
        "block_chars": len(text),
        "block_lines": len(text.splitlines()) or 1,
        "estimated_tokens": max(1, math.ceil(len(text) / 2)),  # 中文约 2 字符/token
        "has_table": bool(block.get("has_table")),
        "section_id": block.get("section_id"),
        "section_path": [str(v)[:200] for v in (block.get("section_path") or [])[-4:]],
        "scope": scope,
        "scope_ambiguity": scope["ambiguous"],
        "preview": preview(text),
    }


def _matched_rows(text: str, query: str) -> tuple[list[str], list[str]]:
    """查询按空格拆成词，返回在 block 里出现的词，以及包含这些词的前 3 行。"""
    def norm(s: str) -> str:
        return unicodedata.normalize("NFKC", s).lower()

    terms = [t for t in query.split() if norm(t) in norm(text)][:8]
    rows = [preview(line, 240) for line in text.splitlines() if any(norm(t) in norm(line)
                                                                     for t in terms)]
    return terms, rows[:3]


# ---------------------------------------------------------------- 工具实现


def search_docs(ctx: ToolContext, args: SearchDocsArgs) -> dict[str, Any]:
    """全库检索，返回融合后的前 top_k 个候选文档，每个文档附最佳 block。"""
    raw = ctx.retriever.search_docs(args.query, args.top_k)
    state = ctx.state

    results = []
    for row in raw["results"]:
        item = dict(row)
        block = ctx.corpus.block(row["doc_id"], row["best_block_id"] or "")
        if block is not None:
            item.update(block_summary(block))
        results.append(item)

    # 记录“已定位”：之后只允许读取搜索结果里出现过的文档 / block（防止模型编造 id）
    located = results + [row for rows in raw["routes"].values() for row in rows[:8]]
    for row in located:
        state.searched_doc_ids.add(row["doc_id"])
        if row.get("best_block_id"):
            state.located_pairs.add((row["doc_id"], row["best_block_id"]))
    state.searched = True
    return {"query": args.query, "results": results}


def _search_one(ctx: ToolContext, doc_id: str, query: str, top_k: int) -> dict[str, Any]:
    rows = []
    for hit in ctx.retriever.search_in_document(doc_id, query, top_k):
        block = ctx.corpus.by_id[hit["block_id"]]
        terms, matched = _matched_rows(str(block["text"]), query)
        rows.append({**hit, "doc_id": doc_id, **block_summary(block),
                     "matched_terms": terms, "matched_rows": matched})
        ctx.state.located_pairs.add((doc_id, hit["block_id"]))
    return {"doc_id": doc_id, "query": query, "results": rows}


def search_in_document(ctx: ToolContext, args: SearchInDocumentArgs) -> dict[str, Any]:
    """在一个已搜到的文档里找 block；queries 模式下每个查询独立返回。"""
    if not ctx.state.searched:
        raise ToolError("search_required", "no successful search_docs call",
                        "call search_docs first")
    if args.doc_id not in ctx.state.searched_doc_ids:
        raise ToolError("document_not_searched",
                        f"doc_id={args.doc_id} was not returned by search_docs",
                        "choose a document from any successful search_docs result")
    if args.query is not None:
        return _search_one(ctx, args.doc_id, args.query, args.top_k)
    return {
        "doc_id": args.doc_id,
        "queries": args.queries,
        "query_results": [_search_one(ctx, args.doc_id, q, args.top_k) for q in args.queries],
    }
