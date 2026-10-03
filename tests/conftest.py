"""测试共用的数据：一个只有 3 个文档的小语料，和一个按剧本回放回复的假模型。

为什么用假模型（FakeLLM）：测试要快、免费、结果每次一样。真实模型每次回答都不同，
没法写断言；我们要测的是 Harness 的逻辑（压缩、停止条件、工具规则），而不是模型聪不聪明。
"""

from __future__ import annotations

from typing import Any

import pytest

from margin.harness import build_registry
from margin.retrieval import Corpus, HybridRetriever
from margin.retrieval.alias import Alias, AliasCatalog

BLOCKS = [
    {
        "doc_id": "jia_2023", "block_id": "jia_2023_b0001", "char_start": 0,
        "section_path": ["第三节 管理层讨论与分析"], "has_table": False,
        "text": "2023年，甲公司实现营业收入120.5亿元，同比增长12.4%。\n"
                "归属于母公司股东的净利润8.2亿元。\n研发投入3.1亿元。",
    },
    {
        "doc_id": "jia_2023", "block_id": "jia_2023_b0002", "char_start": 100,
        "section_path": ["第十节 财务报告", "合并资产负债表"], "has_table": True,
        "text": "| 项目 | 2023年末 | 2022年末 |\n"
                "| 货币资金 | 35.6 | 30.1 |\n| 总资产 | 410.2 | 388.7 |",
    },
    {
        "doc_id": "jia_2022", "block_id": "jia_2022_b0001", "char_start": 0,
        "section_path": ["第三节 管理层讨论与分析"], "has_table": False,
        "text": "2022年，甲公司实现营业收入107.2亿元。\n归属于母公司股东的净利润7.5亿元。",
    },
    {
        "doc_id": "yi_bond", "block_id": "yi_bond_b0001", "char_start": 0,
        "section_path": ["募集说明书", "本期债券基本条款"], "has_table": False,
        "text": "本期债券简称为22乙债01，债券代码185001。\n票面利率为3.15%，期限5年。",
    },
]
TITLES = {"jia_2023": "甲公司2023年年度报告", "jia_2022": "甲公司2022年年度报告",
          "yi_bond": "乙公司2022年公开发行公司债券募集说明书"}


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    return Corpus(BLOCKS, TITLES)


@pytest.fixture(scope="session")
def retriever(corpus: Corpus) -> HybridRetriever:
    aliases = AliasCatalog([
        Alias("yi_bond", "22乙债01", "22乙债01", "security_code", "yi_bond_b0001"),
        Alias("yi_bond", "185001", "185001", "security_code", "yi_bond_b0001"),
    ])
    return HybridRetriever.create(corpus, aliases=aliases)


@pytest.fixture
def make_registry(corpus, retriever):
    """为一道题创建新的工具集（每次都是全新状态）。"""
    def make(task: dict[str, Any] | None = None):
        return build_registry(task or {"question": "测试题", "answer_format": "num"},
                              corpus, retriever)
    return make
