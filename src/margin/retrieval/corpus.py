"""语料：把 blocks.jsonl 读进内存，按 block_id / doc_id 建索引。

一个 block 是文档里的一段连续原文（一般是一个小节或一张表），字段示例：
    {
      "doc_id": "annual_某公司_2023_report",
      "block_id": "annual_某公司_2023_report_b0042",
      "text": "……原文……",
      "section_path": ["第三节 管理层讨论与分析", "一、经营情况"],
      "section_id": "s012",
      "has_table": true,
      "char_start": 18230
    }
Agent 的检索、阅读、引用都以 (doc_id, block_id) 为定位单位。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Corpus:
    blocks: list[dict[str, Any]]
    # doc_id -> 文档标题（来自文件名），参与 BM25 打分，让“公司名 + 年报”这类查询能命中
    titles: dict[str, str] = field(default_factory=dict)
    by_id: dict[str, dict[str, Any]] = field(init=False)
    by_doc: dict[str, list[dict[str, Any]]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {str(b["block_id"]): b for b in self.blocks}
        self.by_doc = {}
        for block in self.blocks:
            self.by_doc.setdefault(str(block["doc_id"]), []).append(block)
        # 同一文档内按原文顺序排列
        for rows in self.by_doc.values():
            rows.sort(key=lambda b: int(b.get("char_start", 0)))

    def block(self, doc_id: str, block_id: str) -> dict[str, Any] | None:
        """按 (doc_id, block_id) 取 block；两者不配套时返回 None。"""
        block = self.by_id.get(block_id)
        if block is None or str(block["doc_id"]) != doc_id:
            return None
        return block

    @classmethod
    def load(cls, blocks_path: Path, manifest_path: Path | None = None) -> Corpus:
        with blocks_path.open(encoding="utf-8") as f:
            blocks = [json.loads(line) for line in f if line.strip()]
        titles: dict[str, str] = {}
        if manifest_path is not None:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # manifest 里记录了每个 doc_id 的源文件路径，文件名就是文档标题
            for doc_id, item in manifest["documents"]["items"].items():
                titles[doc_id] = Path(item["path"]).stem
        return cls(blocks, titles)
