"""BM25 关键词检索（Okapi BM25），用稀疏矩阵实现。

BM25 的直觉：一个词在这个 block 里出现得越多、在整个语料里越少见，这个 block 得分越高；
同时对特别长的 block 做惩罚，避免长文本靠“字多”占便宜。

    score(block, query) = Σ_词 idf(词) × tf × (k1 + 1) / (tf + k1 × (1 - b + b × 块长 / 平均块长))

存储方式：一个 [block 数 × 词表大小] 的稀疏矩阵，格子里是“词频 tf”。
绝大多数格子是 0，所以用 scipy 的稀疏矩阵（只存非零值），内存小、按列取词很快。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import jieba
import numpy as np
from scipy import sparse

K1 = 1.5
B = 0.75
# idf 为负（词太常见）时，用平均 idf × EPSILON 兜底，和 rank_bm25 的做法一致。
EPSILON = 0.25

jieba.setLogLevel(60)  # 关掉 jieba 加载词典时的日志


def tokenize(text: str) -> list[str]:
    """分词 = jieba 词 + 相邻两字（bigram）。

    只用 jieba 的话，"营业收入" 可能被切成一个词，而查询写 "营收" 就匹配不上；
    再加上两字切片（"营业"、"业收"、"收入"），能显著提高中文召回。
    """
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    words = [w for w in jieba.cut(normalized) if w.strip()]
    compact = normalized.replace(" ", "")
    bigrams = [compact[i : i + 2] for i in range(len(compact) - 1)]
    return words + bigrams


class BM25Index:
    def __init__(self, tf: sparse.csc_matrix, vocab: dict[str, int]) -> None:
        self.tf = tf  # CSC 格式：按列（按词）取数据最快
        self.vocab = vocab
        n_blocks = tf.shape[0]
        doc_len = np.asarray(tf.sum(axis=1)).ravel()
        # 每个 block 的长度惩罚项，提前算好，查询时直接用
        self.norm = K1 * (1 - B + B * doc_len / doc_len.mean())
        # 文档频率 df：每个词出现在多少个 block 里
        df = np.diff(tf.indptr)
        idf = np.log((n_blocks - df + 0.5) / (df + 0.5))
        idf[idf < 0] = EPSILON * idf.mean()
        self.idf = idf

    @classmethod
    def build(cls, texts: list[str]) -> BM25Index:
        """从 block 文本建索引。真实语料（1.7 万个 block）首次构建需要几分钟。"""
        vocab: dict[str, int] = {}
        rows, cols, counts = [], [], []
        for row, text in enumerate(texts):
            counter = Counter(tokenize(text))
            ids = [vocab.setdefault(token, len(vocab)) for token in counter]
            rows.append(np.full(len(ids), row, dtype=np.int32))
            cols.append(np.asarray(ids, dtype=np.int32))
            counts.append(np.fromiter(counter.values(), dtype=np.float32, count=len(ids)))
        tf = sparse.csc_matrix(
            (np.concatenate(counts), (np.concatenate(rows), np.concatenate(cols))),
            shape=(len(texts), len(vocab)),
        )
        return cls(tf, vocab)

    def scores(self, query: str) -> np.ndarray:
        """返回每个 block 对这个查询的 BM25 分数（长度 = block 数）。"""
        result = np.zeros(self.tf.shape[0], dtype=np.float64)
        for token in tokenize(query):  # 查询里重复的词会重复计分，与 rank_bm25 一致
            col = self.vocab.get(token)
            if col is None:
                continue
            start, end = self.tf.indptr[col], self.tf.indptr[col + 1]
            rows = self.tf.indices[start:end]  # 包含这个词的 block 下标
            tf = self.tf.data[start:end]  # 对应的词频
            result[rows] += self.idf[col] * tf * (K1 + 1) / (tf + self.norm[rows])
        return result

    # ---- 持久化：避免每次启动都重新分词 ----

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        sparse.save_npz(directory / "tf.npz", self.tf)
        # Windows 默认编码是 GBK，必须显式指定 utf-8，否则写中文词表会报错
        (directory / "vocab.json").write_text(
            json.dumps(self.vocab, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path) -> BM25Index:
        tf = sparse.load_npz(directory / "tf.npz").tocsc()
        vocab = json.loads((directory / "vocab.json").read_text(encoding="utf-8"))
        return cls(tf, vocab)


def top_indices(scores: np.ndarray, limit: int) -> list[int]:
    """取分数最高且大于 0 的前 limit 个下标，按分数从高到低。"""
    positive = np.flatnonzero(scores > 0)
    if len(positive) > limit:
        positive = positive[np.argpartition(scores[positive], -limit)[-limit:]]
    return sorted(positive.tolist(), key=lambda i: -scores[i])
