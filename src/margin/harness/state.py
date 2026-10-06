"""一次 episode（解一道题的完整过程）里，工具之间共享的状态。

每道题新建一个 EpisodeState，不同用户、不同题目的笔记和证据互不干扰。
工具通过它实现几条“规矩”，例如：
    - 先 search_docs，才能 search_in_document / read_section
    - 只能读搜索结果里出现过的 (doc_id, block_id)
    - cite 的原文必须是之前读到过的（visible_texts）
    - 写完笔记后，必须先调用一次别的工具，才能再写笔记
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EpisodeState:
    # 题目：question / options / answer_format（finalize 按它规范化答案）
    #       / require_citation（产品模式：没有引用就交答案时提醒一次）
    task: dict[str, Any]

    # ---- 检索与阅读的进度 ----
    searched: bool = False  # 是否成功调用过 search_docs
    searched_doc_ids: set[str] = field(default_factory=set)  # 搜索结果里出现过的文档
    located_pairs: set[tuple[str, str]] = field(default_factory=set)  # 可读取的 (doc, block)
    visible_texts: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # 读过的原文
    citations: list[dict[str, Any]] = field(default_factory=list)  # 成功的引用
    citation_reminded: bool = False  # 产品模式：已经因为没有引用被 finalize 拒过一次

    # ---- 工作笔记（RC6-C 的核心：写笔记 = 压缩上下文）----
    note: str | None = None
    note_revision: int = 0
    note_allowed: bool = False  # 上次写笔记之后，是否又有非 write_note 工具成功返回

    # ---- 终止 ----
    terminal: str | None = None  # "finalize" / "escalate"
    final_answers: list[str] = field(default_factory=list)
