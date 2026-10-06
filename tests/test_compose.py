"""撰写回答的代码校验：模型写的回答进库之前要过这一关。"""

from margin.compose import check

CITATIONS = [{"no": 1, "doc_id": "gs_2022", "block_id": "gs_2022_b0042",
              "match": "营业收入 | 12,046,275.40", "before": "合并利润表 ", "after": " |"}]
FINAL = {"name": "finalize", "submitted": ["1204.63"], "raw": ["1204.63"]}


def test_unknown_refs_are_removed_and_unsourced_numbers_flagged():
    """[3] 没有对应的来源卡片：删掉，否则页面上点了没反应；9.9% 在材料里找不到：记为未核实。
    带千分位的 12,046,275.40 和原文写法一致，算有出处。"""
    text = ("广晟控股2022年营业收入为1204.63亿元。\n\n"
            "合并利润表“营业收入”为12,046,275.40万元[1][3]，同比增长9.9%。")

    written = check(text, "广晟控股2022年营业收入是多少亿元？", FINAL, CITATIONS, [])

    assert "[3]" not in written["text"] and "[1]" in written["text"]
    assert (written["citations"], written["unverified"]) == ([1], ["9.9"])
