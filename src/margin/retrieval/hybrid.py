"""混合检索：三路召回各自给出文档排名，再用 RRF 融合成最终的前 8 个文档。

三路：
    block_bm25          BM25 在 block 级打分，每个文档取得分最高的 block 代表它
    exact_entity        查询里出现了文档的实体别名（公司名/证券代码……）
    chroma_dense_block  向量相似度（可选）

RRF（Reciprocal Rank Fusion，倒数排名融合）：
    文档得分 = Σ_各路 1 / (60 + 该文档在这一路的名次)
只看名次、不看原始分数，所以不同量纲的分数（BM25 分、余弦相似度）可以直接融合。
每一路只取前 8 名参与融合（fusion window = 8），这是实验里在开发集上效果最好的设置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .alias import AliasCatalog
from .bm25 import BM25Index, top_indices
from .corpus import Corpus
from .dense import DenseSearcher

RRF_K = 60
FUSION_WINDOW = 8
ROUTE_LIMIT = 64  # 每一路最多保留多少个文档


def block_search_text(corpus: Corpus, block: dict[str, Any]) -> str:
    """参与 BM25 的文本 = 文档标题 + 章节路径 + 正文。"""
    title = corpus.titles.get(str(block["doc_id"]), "")
    sections = [str(s) for s in block.get("section_path") or []]
    return "\n".join([title, *sections, str(block["text"])])


class HybridRetriever:
    def __init__(
        self,
        corpus: Corpus,
        bm25: BM25Index,
        aliases: AliasCatalog | None = None,
        dense: DenseSearcher | None = None,
    ) -> None:
        self.corpus = corpus
        self.bm25 = bm25
        self.aliases = aliases
        self.dense = dense
        # doc_id -> 这个文档的 block 在 BM25 矩阵里的行号，文档内检索时用
        self.doc_rows: dict[str, np.ndarray] = {}
        for row, block in enumerate(corpus.blocks):
            self.doc_rows.setdefault(str(block["doc_id"]), []).append(row)
        self.doc_rows = {k: np.asarray(v) for k, v in self.doc_rows.items()}

    @classmethod
    def create(
        cls,
        corpus: Corpus,
        *,
        cache_dir: Path | None = None,
        aliases: AliasCatalog | None = None,
        dense: DenseSearcher | None = None,
    ) -> HybridRetriever:
        """有缓存就加载 BM25 索引，没有就构建并写入缓存。"""
        if cache_dir is not None and (cache_dir / "tf.npz").exists():
            bm25 = BM25Index.load(cache_dir)
        else:
            bm25 = BM25Index.build([block_search_text(corpus, b) for b in corpus.blocks])
            if cache_dir is not None:
                bm25.save(cache_dir)
        return cls(corpus, bm25, aliases, dense)

    # ------------------------------------------------------------ search_docs

    def search_docs(self, query: str, top_k: int = 8) -> dict[str, Any]:
        routes = {"block_bm25": self._bm25_route(query)}
        if self.aliases is not None:
            routes["exact_entity"] = self.aliases.search(query)[:ROUTE_LIMIT]
        if self.dense is not None:
            routes["chroma_dense_block"] = self._dense_route(query)
        return {"routes": routes, "results": rrf_fuse(routes, top_k)}

    def _bm25_route(self, query: str) -> list[dict[str, Any]]:
        scores = self.bm25.scores(query)
        # 先取足够多的高分 block，再按文档去重，每个文档保留最高分的 block
        rows = [
            (str(self.corpus.blocks[i]["doc_id"]), str(self.corpus.blocks[i]["block_id"]),
             float(scores[i]))
            for i in top_indices(scores, ROUTE_LIMIT * 8)
        ]
        return best_block_per_doc(rows)

    def _dense_route(self, query: str) -> list[dict[str, Any]]:
        return best_block_per_doc(self.dense.search(query, ROUTE_LIMIT * 4))

    # ------------------------------------------------------ search_in_document

    def search_in_document(self, doc_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """只在一个文档内部按 BM25 排 block。"""
        rows = self.doc_rows[doc_id]  # doc_id 不存在会抛 KeyError，工具层会转成 not_found
        scores = self.bm25.scores(query)[rows]
        order = [i for i in np.argsort(-scores, kind="stable") if scores[i] > 0][:top_k]
        return [
            {"block_id": str(self.corpus.blocks[rows[i]]["block_id"]), "rank": rank,
             "score": float(scores[i])}
            for rank, i in enumerate(order, 1)
        ]


def best_block_per_doc(hits: list[tuple[str, str, float]]) -> list[dict[str, Any]]:
    """[(doc_id, block_id, score)] 按分数降序 -> 每个文档一行，记录最佳 block。"""
    docs: dict[str, dict[str, Any]] = {}
    for doc_id, block_id, score in sorted(hits, key=lambda h: -h[2]):
        if doc_id not in docs:
            docs[doc_id] = {"doc_id": doc_id, "score": score, "best_block_id": block_id}
    return list(docs.values())[:ROUTE_LIMIT]


def rrf_fuse(routes: dict[str, list[dict[str, Any]]], top_k: int) -> list[dict[str, Any]]:
    """把各路排名融合成最终文档列表。"""
    fused: dict[str, dict[str, Any]] = {}
    for route_name, rows in routes.items():
        for rank, row in enumerate(rows[:FUSION_WINDOW], 1):
            doc = fused.setdefault(
                row["doc_id"], {"doc_id": row["doc_id"], "score": 0.0, "contributions": {}}
            )
            doc["score"] += 1.0 / (RRF_K + rank)
            doc["contributions"][route_name] = rank
            # 代表 block 取第一个给出 block 的路（别名路不提供 block）
            if "best_block_id" not in doc and row.get("best_block_id"):
                doc["best_block_id"] = row["best_block_id"]
    ranked = sorted(fused.values(), key=lambda d: (-d["score"], d["doc_id"]))[:top_k]
    for rank, doc in enumerate(ranked, 1):
        doc["rank"] = rank
        if "best_block_id" not in doc:
            # 只被别名路命中前 8 的文档：去其他路的完整名单里找它的最佳 block
            doc["best_block_id"] = next(
                (r["best_block_id"] for rows in routes.values() for r in rows
                 if r["doc_id"] == doc["doc_id"] and r.get("best_block_id")),
                None,
            )
    return ranked
