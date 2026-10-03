"""实体别名精确匹配：查询里出现了某文档的“身份名称”，就直接召回这个文档。

为什么需要：BM25 对“公司全称、债券简称、证券代码”这类实体不敏感，
比如查询 "22国开05 票面利率" 时，"22国开05" 被拆碎后很难精确命中。
别名表是离线从原文中抽取的（公司名、产品名、文号、证券代码……），每条都带原文出处。

别名表每行示例：
    {"doc_id": "12", "alias": "众安在线财产保险股份有限公司",
     "normalized_alias": "众安在线财产保险股份有限公司",
     "entity_type": "organization", "source_block_id": "12_b0001", ...}
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


def normalize_alias(text: str) -> str:
    """统一成：半角、小写、只保留字母数字和汉字。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^0-9a-z一-鿿]+", "", text)


@dataclass(frozen=True)
class Alias:
    doc_id: str
    alias: str
    normalized: str
    entity_type: str
    source_block_id: str


class AliasCatalog:
    def __init__(self, aliases: list[Alias]) -> None:
        # 长别名优先：先匹配 "中国平安财产保险股份有限公司"，再考虑 "中国平安"
        self.aliases = sorted(aliases, key=lambda a: (-len(a.normalized), a.alias))

    @classmethod
    def load(cls, path: Path) -> AliasCatalog:
        aliases = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    aliases.append(
                        Alias(
                            doc_id=str(row["doc_id"]),
                            alias=row["alias"],
                            normalized=row["normalized_alias"],
                            entity_type=row["entity_type"],
                            source_block_id=row["source_block_id"],
                        )
                    )
        return cls(aliases)

    def search(self, query: str) -> list[dict]:
        """返回命中的文档列表，按命中的最长别名长度排序。"""
        q = normalize_alias(query)
        selected: list[Alias] = []
        for alias in self.aliases:
            if not _contains(q, alias):
                continue
            # 已经命中了更长的别名（如“中国平安财产保险”），它包含的短别名（“中国平安”）不再重复算
            if any(alias.normalized in kept.normalized for kept in selected):
                continue
            selected.append(alias)

        by_doc: dict[str, list[Alias]] = {}
        for alias in selected:
            by_doc.setdefault(alias.doc_id, []).append(alias)
        rows = [
            {
                "doc_id": doc_id,
                "score": float(max(len(a.normalized) for a in items)),
                "matched_entities": [a.normalized for a in items],
            }
            for doc_id, items in by_doc.items()
        ]
        rows.sort(key=lambda r: (-r["score"], r["doc_id"]))
        return rows


def _is_code_char(c: str) -> bool:
    """英文字母或数字（代码的组成字符）。空字符串返回 False。"""
    return c.isascii() and c.isalnum()


def _contains(query: str, alias: Alias) -> bool:
    """文号、证券代码要求边界完整：代码 "600000" 不能命中查询里的 "1600000"。"""
    if alias.entity_type not in {"document_number", "security_code"}:
        return alias.normalized in query
    for m in re.finditer(re.escape(alias.normalized), query):
        before = query[m.start() - 1] if m.start() > 0 else ""
        after = query[m.end()] if m.end() < len(query) else ""
        if not _is_code_char(before) and not _is_code_char(after):
            return True
    return False
