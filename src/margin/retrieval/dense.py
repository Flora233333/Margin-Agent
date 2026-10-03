"""向量检索（可选的第三路）：把查询变成向量，在 Chroma 里找语义最接近的 block。

BM25 只认字面，"营收增长" 和 "营业收入同比增加" 字面重合少；向量检索能补上这类语义相近的情况。

需要两样东西：
    1. embedding 服务：llama.cpp llama-server 的 /v1/embeddings（OpenAI 兼容）
    2. 已建好的 Chroma 向量库：每个 block 一个向量，id = block_id，metadata 里有 doc_id
两者都要和 RC6-C 当前用的索引 rc6-local-qwen3emb06b-q8_0-v2 一致：同一个 Q8_0 GGUF、
last-token 池化、L2 归一化；查询格式见 embed_query。
没有配置时，HybridRetriever 会自动跳过这一路。
"""

from __future__ import annotations

import httpx

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
    def __init__(self, chroma_path: str, embedder: EmbeddingClient,
                 collection: str = "afacpt_canonical_blocks_v1") -> None:
        import chromadb  # 可选依赖，只有启用向量检索时才导入

        self.collection = chromadb.PersistentClient(path=chroma_path).get_collection(collection)
        self.embedder = embedder

    def search(self, query: str, limit: int) -> list[tuple[str, str, float]]:
        """返回 [(doc_id, block_id, 相似度), ...]，相似度 = 1 - 余弦距离。"""
        response = self.collection.query(
            query_embeddings=[self.embedder.embed_query(query)],
            n_results=limit,
            include=["metadatas", "distances"],
        )
        return [
            (str(meta["doc_id"]), block_id, 1.0 - float(distance))
            for block_id, meta, distance in zip(
                response["ids"][0], response["metadatas"][0], response["distances"][0],
                strict=True,
            )
        ]
