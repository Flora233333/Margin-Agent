"""检索测试：BM25 排序、别名精确匹配、RRF 融合。"""

from margin.retrieval.alias import Alias, AliasCatalog
from margin.retrieval.hybrid import rrf_fuse


def test_bm25_ranks_matching_document_first(retriever):
    results = retriever.search_docs("甲公司 2022年 营业收入")["results"]
    assert results[0]["doc_id"] == "jia_2022"


def test_alias_route_hits_security_code(retriever):
    routes = retriever.search_docs("185001 的票面利率")["routes"]
    assert routes["exact_entity"][0]["doc_id"] == "yi_bond"


def test_security_code_needs_clean_boundary():
    catalog = AliasCatalog([Alias("d", "185001", "185001", "security_code", "b")])
    assert catalog.search("代码185001") != []
    assert catalog.search("1850012") == []  # 更长的数字里包含它，不算命中


def test_rrf_rewards_documents_found_by_several_routes():
    routes = {
        "block_bm25": [{"doc_id": "a", "best_block_id": "a1"},
                       {"doc_id": "b", "best_block_id": "b1"}],
        "exact_entity": [{"doc_id": "b"}],
    }
    fused = rrf_fuse(routes, top_k=8)
    # b 在 BM25 只排第 2，但两路都命中，融合后排第 1
    assert [d["doc_id"] for d in fused] == ["b", "a"]
    assert fused[0]["best_block_id"] == "b1"


def test_search_in_document_stays_inside_the_document(retriever):
    hits = retriever.search_in_document("jia_2023", "货币资金 总资产")
    assert hits[0]["block_id"] == "jia_2023_b0002"
    assert all(h["block_id"].startswith("jia_2023") for h in hits)
