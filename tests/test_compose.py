"""撰写回答的代码校验：模型写的回答进库之前要过这一关。"""

from margin.compose import check, citations_of
from margin.harness import Step

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


def test_cited_table_row_comes_with_its_header():
    """引的是表格里的一行：给撰写模型的前文是表头 + 分隔行，而不是前 60 个字。
    按字数截会截掉“2025年12月31日 | 2024年12月31日”，模型分不清列，在思考里反复推列的顺序，
    token 用完也没写出正文（run 94）。"""
    header = ("| 资产质量指标(%) | 2025年12月31日 | 2024年12月31日 | 本年末比上年末增减 "
              "| 2023年12月31日 |\n| --- | --- | --- | --- | --- |\n")
    table = ("资产质量\n" + header
             + "| 正常贷款率 | 98.87 | 98.88 | 下降0.01个百分点 | 98.90 |\n"
             "| 不良贷款率 | 0.94 | 0.95 | 下降0.01个百分点 | 0.95 |")
    block = {"doc_id": "cmb_2025", "block_id": "cmb_2025_b0018"}
    steps = [
        Step(turn=0, reasoning=None, tool_name="read_section", arguments="{}",
             result={"ok": True, "data": {**block, "text": table}}),
        Step(turn=1, reasoning=None, tool_name="cite", arguments="{}",
             result={"ok": True, "data": {**block, "grounded": True,
                                           "matched_text": "| 不良贷款率 | 0.94 | 0.95 |"}}),
    ]

    [citation] = citations_of(steps)

    assert citation["before"] == header + "…\n"  # 中间隔着的“正常贷款率”一行写成 …
