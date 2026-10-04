"""向量检索的集成测试：连真实 PostgreSQL + pgvector（先 docker compose up -d postgres）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

import numpy as np
import pytest
from sqlalchemy import insert

from margin.models import BlockVector
from margin.retrieval.dense import DenseSearcher

pytestmark = pytest.mark.integration


def unit(*values: float) -> np.ndarray:
    """构造一个 1024 维向量：前几维给定，其余为 0，再归一化成单位长度。"""
    vector = np.zeros(1024, dtype=np.float32)
    vector[: len(values)] = values
    return vector / np.linalg.norm(vector)


class FakeEmbedder:
    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector

    def embed_query(self, query: str) -> list[float]:
        return self.vector.tolist()


def test_dense_search_ranks_blocks_by_cosine_similarity(db):
    with db.begin() as conn:
        conn.execute(insert(BlockVector), [
            {"block_id": "a_b1", "doc_id": "a", "embedding": unit(1, 0)},
            {"block_id": "a_b2", "doc_id": "a", "embedding": unit(1, 1)},
            {"block_id": "b_b1", "doc_id": "b", "embedding": unit(0, 1)},
        ])

    results = DenseSearcher(db, FakeEmbedder(unit(1, 0.1))).search("营业收入", limit=2)

    assert [(doc_id, block_id) for doc_id, block_id, _ in results] == [
        ("a", "a_b1"), ("a", "a_b2")]
    assert results[0][2] == pytest.approx(float(unit(1, 0.1) @ unit(1, 0)), abs=1e-6)
