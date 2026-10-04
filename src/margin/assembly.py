"""组件组装：按配置把语料、检索器、模型客户端拼起来。

脚本（scripts/run_episode.py）和后台 worker 用的是同一套组件，组装代码只写这一份，
两边不会因为各自拼装而出现“脚本里能答对、服务里答错”的差异。
"""

from __future__ import annotations

from .db import get_engine
from .llm import OpenAICompatibleClient
from .retrieval import Corpus, HybridRetriever
from .retrieval.alias import AliasCatalog
from .retrieval.dense import DenseSearcher, EmbeddingClient
from .settings import Settings


def build_retriever(settings: Settings) -> tuple[Corpus, HybridRetriever]:
    """加载语料和三路检索。首次运行要构建 BM25 索引（几分钟），之后从缓存加载（约 1 秒）。"""
    corpus = Corpus.load(settings.blocks_path, settings.manifest_path)
    aliases = AliasCatalog.load(settings.alias_path)
    dense = None
    if settings.embedding_base_url:  # 配了 embedding 服务才开向量检索
        embedder = EmbeddingClient(settings.embedding_base_url, settings.embedding_model)
        dense = DenseSearcher(get_engine(), embedder)
    retriever = HybridRetriever.create(
        corpus, cache_dir=settings.cache_dir / "bm25", aliases=aliases, dense=dense
    )
    return corpus, retriever


def build_llm(settings: Settings, model: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        settings.llm_base_url, model, settings.llm_api_key.get_secret_value()
    )
