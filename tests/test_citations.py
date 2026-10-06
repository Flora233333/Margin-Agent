"""引用编号（citations.py）：前端来源卡片和撰写回答依赖同一套编号，编错了 [n] 就会指向别的原文。"""

from margin.citations import CitationNumbers, number_all


def cite_result(matched_text, grounded=True, block_id="jia_2023_b0001"):
    return {"ok": True, "data": {"doc_id": "jia_2023", "block_id": block_id,
                                 "matched_text": matched_text, "grounded": grounded}}


def test_failed_and_ungrounded_cites_get_no_number_and_numbers_do_not_skip():
    """没通过校验的引用不编号；之后的编号从 1 接着排，不会跳成 2。"""
    steps = [
        {"tool_name": "search_docs", "result": {"ok": True, "data": {}}},
        {"tool_name": "cite", "result": {"ok": False, "error": "quote_not_found"}},
        {"tool_name": "cite", "result": cite_result("实现营业收入120.5亿元")},
        {"tool_name": "cite", "result": cite_result("同比增长12.4%")},
    ]
    assert number_all(steps) == [None, None, 1, 2]


def test_citing_the_same_text_twice_gets_one_number():
    """模型有时重复确认同一处原文：只编一个号；同样的文字出自另一块，算另一处原文。"""
    numbers = CitationNumbers()
    assert numbers.number("cite", cite_result("营业收入")) == 1
    assert numbers.number("cite", cite_result("营业收入")) is None
    assert numbers.number("cite", cite_result("营业收入", block_id="jia_2023_b0002")) == 2
