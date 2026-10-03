"""向量检索的集成测试：连真实 PostgreSQL + pgvector（先 docker compose up -d postgres）。

默认不跑；运行：conda run -n margin pytest -m integration
测试在独立的 schema 里建表，不碰真实的 block_vectors 数据。
"""

import os

import numpy as np
import psycopg
import pytest

from margin.retrieval.dense import DenseSearcher

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get(
    "MARGIN_TEST_DATABASE_URL", "postgresql://margin:margin_dev@127.0.0.1:5432/margin")
SCHEMA = "margin_test_dense"


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


@pytest.fixture
def searcher():
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
        admin.execute(f"CREATE TABLE {SCHEMA}.block_vectors (block_id text PRIMARY KEY, "
                      "doc_id text NOT NULL, embedding vector(1024) NOT NULL)")
        rows = [("a_b1", "a", unit(1, 0)), ("a_b2", "a", unit(1, 1)), ("b_b1", "b", unit(0, 1))]
        for block_id, doc_id, vector in rows:
            admin.execute(f"INSERT INTO {SCHEMA}.block_vectors VALUES (%s, %s, %s::vector)",
                          (block_id, doc_id, str(vector.tolist())))
        # search_path 指向测试 schema，DenseSearcher 里的 block_vectors 就是测试表
        url = f"{DATABASE_URL}?options=-csearch_path%3D{SCHEMA},public"
        yield lambda query_vector: DenseSearcher(url, FakeEmbedder(query_vector))
        admin.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def test_dense_search_ranks_blocks_by_cosine_similarity(searcher):
    results = searcher(unit(1, 0.1)).search("营业收入", limit=2)

    assert [(doc_id, block_id) for doc_id, block_id, _ in results] == [
        ("a", "a_b1"), ("a", "a_b2")]
    assert results[0][2] == pytest.approx(float(unit(1, 0.1) @ unit(1, 0)), abs=1e-6)
