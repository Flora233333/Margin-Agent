"""理解题目：模型回复的解析和失败退回。"""

from fakes import FakeLLM

from margin.understand import understand

QUESTION = "广晟控股2022年的营业收入是多少亿元？"


def test_reply_wrapped_in_code_block_is_parsed():
    """模型常把 JSON 包在 ```json 代码块里、前面还带一句话：照样取出标题和格式。"""
    llm = FakeLLM([], understand='好的。\n```json\n{"title": "广晟控股 2022 年营业收入", '
                                 '"label": "广晟控股 · 2022 年营业收入（亿元）", '
                                 '"answer_format": "num"}\n```')

    understood = understand(llm, QUESTION)

    assert (understood.title, understood.answer_format) == ("广晟控股 2022 年营业收入", "num")
    assert understood.label == "广晟控股 · 2022 年营业收入（亿元）"


def test_unusable_reply_falls_back_instead_of_failing():
    """格式不认识（或网关报错）：退回问题前 20 个字 + 文本格式，不能让这道题因此失败。"""
    llm = FakeLLM([], understand='{"title": "广晟控股营收", "answer_format": "money"}')

    understood = understand(llm, QUESTION)

    assert (understood.title, understood.answer_format) == (QUESTION[:20], "text")
