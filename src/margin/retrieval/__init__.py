"""检索层：语料加载 + 三路召回（BM25 / 实体别名 / 向量）+ RRF 融合。"""

from .corpus import Corpus
from .hybrid import HybridRetriever

__all__ = ["Corpus", "HybridRetriever"]
