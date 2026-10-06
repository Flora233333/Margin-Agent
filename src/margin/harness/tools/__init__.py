"""十个工具。顺序与 tool_schemas.json 一致：

    search_docs         全库检索文档
    search_in_document  文档内检索 block
    read_section        读原文
    find_in_block       块内找关键词
    cite                记录原文引用
    compute             确定性计算
    date_calc           日期 / 期限计算
    write_note          写工作笔记（同时压缩上下文）
    finalize            提交答案，结束
    escalate            放弃作答，结束
"""

from __future__ import annotations

from ...retrieval import Corpus, HybridRetriever
from ..state import EpisodeState
from . import args as a
from .actions import compute, date_calc, escalate, finalize, write_note
from .base import ToolContext, ToolRegistry
from .reading import cite, find_in_block, read_section
from .search import search_docs, search_in_document

# 工具名 -> (实现函数, 参数模型)
TOOLS = {
    "search_docs": (search_docs, a.SearchDocsArgs),
    "search_in_document": (search_in_document, a.SearchInDocumentArgs),
    "read_section": (read_section, a.ReadSectionArgs),
    "find_in_block": (find_in_block, a.FindInBlockArgs),
    "cite": (cite, a.CiteArgs),
    "compute": (compute, a.ComputeArgs),
    "date_calc": (date_calc, a.DateCalcArgs),
    "write_note": (write_note, a.WriteNoteArgs),
    "finalize": (finalize, a.FinalizeArgs),
    "escalate": (escalate, a.EscalateArgs),
}


def build_registry(task: dict, corpus: Corpus, retriever: HybridRetriever,
                   tools: dict = TOOLS) -> ToolRegistry:
    """为一道题创建工具集：每道题一个新的 EpisodeState，状态互不影响。

    tools：可以替换某个工具的实现（worker 给 finalize 包一层“等答案格式”），工具说明不变。
    """
    return ToolRegistry(ToolContext(corpus, retriever, EpisodeState(task)), tools)


__all__ = ["TOOLS", "ToolRegistry", "build_registry"]
