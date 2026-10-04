"""向量检索（可选的第三路）：把查询变成向量，在 PostgreSQL（pgvector）里找语义最接近的 block。

BM25 只认字面，"营收增长" 和 "营业收入同比增加" 字面重合少；向量检索能补上这类语义相近的情况。

需要两样东西：
    1. embedding 服务：llama.cpp llama-server 的 /v1/embeddings（OpenAI 兼容），只给“查询”算向量
    2. PG 里的 block_vectors 表：每个 block 一行向量，由 scripts/import_vectors.py 从
       RC6-C 当前用的索引 rc6-local-qwen3emb06b-q8_0-v2 原样导入（文档向量不重新计算）
查询向量必须和建索引时同一套配置：同一个 Q8_0 GGUF、last-token 池化、L2 归一化，
查询格式见 embed_query。
没有配置时，HybridRetriever 会自动跳过这一路。

为什么用精确检索、不建 HNSW 索引（D15）：
    1.76 万条向量，PG 顺序扫一遍算余弦距离约 40ms，比一次模型调用（秒级）小两个数量级。
    HNSW 是近似检索，ef_search 设小了会漏结果（pgvector 默认 40，而我们一次要 256 条）。
    精确检索没有参数要调，结果也最稳定；语料到几十万块时再加索引。
"""

from __future__ import annotations

import httpx
from sqlalchemy import Engine, select

from ..models import BlockVector

# Qwen3-Embedding 要求查询带指令前缀（文档侧不带），否则召回质量明显下降。
# 拼接方式照抄 RC6-C（afacpt.harness.local_embedding）：指令 + 一个空格 + 查询。
# 少一个空格，token 序列就不同，向量会和训练环境有细微偏差。
QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, timeout: float = 30) -> None:
        self.model = model
        self.http = httpx.Client(base_url=base_url, timeout=timeout)

    def embed_query(self, query: str) -> list[float]:
        response = self.http.post(
            "/embeddings", json={"model": self.model, "input": [f"{QUERY_INSTRUCTION} {query}"]}
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]


class DenseSearcher:
    def __init__(self, engine: Engine, embedder: EmbeddingClient) -> None:
        self.engine = engine  # 连接从 Engine 的连接池里借，用完归还
        self.embedder = embedder

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        """返回 [(doc_id, block_id, 相似度), ...]，按相似度从高到低。

        cosine_distance 对应 pgvector 的 `<=>` 余弦距离运算符（0 = 方向完全相同），
        相似度 = 1 - 距离。
        生成的 SQL：SELECT doc_id, block_id, 1 - (embedding <=> :v) ... ORDER BY embedding <=> :v
        """
        distance = BlockVector.embedding.cosine_distance(self.embedder.embed_query(query))
        stmt = (select(BlockVector.doc_id, BlockVector.block_id, 1 - distance)
                .order_by(distance).limit(limit))
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [(doc_id, block_id, float(similarity)) for doc_id, block_id, similarity in rows]
