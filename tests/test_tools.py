"""工具规则测试：每条规则对应一种模型会犯的错误。"""

import json

import pytest

from margin.harness.tools import TOOLS
from margin.harness.tools.base import TOOL_SCHEMAS


def run(registry, name, **args):
    return registry.execute(name, json.dumps(args, ensure_ascii=False))


def test_schema_and_argument_models_agree():
    """发给模型的 schema 与实际校验参数的 Pydantic 模型必须一致，否则模型照着说明调用也会报错。"""
    assert [s["function"]["name"] for s in TOOL_SCHEMAS] == list(TOOLS)
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        params = schema["function"]["parameters"]
        model = TOOLS[name][1]
        assert set(params["properties"]) == set(model.model_fields), name
        required = {f for f, info in model.model_fields.items() if info.is_required()}
        assert set(params.get("required", [])) == required, name


def test_read_requires_search_first(make_registry):
    result = run(make_registry(), "read_section", doc_id="jia_2023", block_id="jia_2023_b0001")
    assert result["error"] == "search_required"


def test_cannot_read_block_never_returned_by_search(make_registry):
    registry = make_registry()
    run(registry, "search_docs", query="185001")  # 只会命中乙公司债券
    result = run(registry, "read_section", doc_id="jia_2022", block_id="jia_2022_b0001")
    assert result["error"] == "block_not_located"


def test_bad_arguments_are_reported_not_raised(make_registry):
    result = run(make_registry(), "search_docs", query="甲公司", unknown_field=1)
    assert result["ok"] is False
    assert result["error"] == "bad_args"


def test_read_section_paginates_by_lines(make_registry):
    registry = make_registry()
    run(registry, "search_docs", query="甲公司 2023 营业收入")
    first = run(registry, "read_section", doc_id="jia_2023", block_id="jia_2023_b0001",
                max_chars=100)["data"]
    assert first["complete"] is True
    assert first["next_row_offset"] is None
    second = run(registry, "read_section", doc_id="jia_2023", block_id="jia_2023_b0001",
                 row_offset=1)["data"]
    assert second["text"].startswith("归属于母公司")


def test_cite_requires_text_seen_before(make_registry):
    registry = make_registry()
    run(registry, "search_docs", query="甲公司 2023 营业收入")
    quote = "实现营业收入120.5亿元"
    before = run(registry, "cite", doc_id="jia_2023", block_id="jia_2023_b0001", quote=quote)
    assert before["error"] == "quote_not_visible"

    run(registry, "read_section", doc_id="jia_2023", block_id="jia_2023_b0001")
    after = run(registry, "cite", doc_id="jia_2023", block_id="jia_2023_b0001", quote=quote)
    assert after["ok"] is True
    assert after["data"]["level"] == "exact"


def test_cite_tolerates_whitespace_but_not_paraphrase(make_registry):
    registry = make_registry()
    run(registry, "search_docs", query="甲公司 2023 营业收入")
    run(registry, "read_section", doc_id="jia_2023", block_id="jia_2023_b0001")
    spaced = run(registry, "cite", doc_id="jia_2023", block_id="jia_2023_b0001",
                 quote="实现营业收入 120.5 亿元")
    assert spaced["ok"] is True
    paraphrased = run(registry, "cite", doc_id="jia_2023", block_id="jia_2023_b0001",
                      quote="营业收入达到了120.5亿元")
    assert paraphrased["error"] == "quote_not_found"
    assert paraphrased["detail"]["suggested_quote"]  # 给出可照抄的原文建议


def test_find_in_block_returns_context_around_keyword(make_registry):
    registry = make_registry()
    run(registry, "search_docs", query="甲公司 2023 营业收入")
    data = run(registry, "find_in_block", doc_id="jia_2023", block_id="jia_2023_b0001",
               keywords=["研发投入"], before_chars=0, after_chars=10)["data"]
    assert data["matches_returned"] == 1
    assert data["matches"][0]["text"].startswith("研发投入3.1亿元")


def test_write_note_needs_new_progress_between_updates(make_registry):
    registry = make_registry()
    assert run(registry, "write_note", note="第一版")["error"] == (
        "write_note_requires_new_non_note_result")
    run(registry, "search_docs", query="甲公司")
    assert run(registry, "write_note", note="第一版")["ok"] is True
    assert run(registry, "write_note", note="第二版")["ok"] is False


def test_search_in_document_only_for_searched_docs(make_registry):
    registry = make_registry()
    run(registry, "search_docs", query="185001")  # 只会命中乙公司债券
    assert run(registry, "search_in_document", doc_id="jia_2023", query="营业收入")[
        "error"] == "document_not_searched"
    data = run(registry, "search_in_document", doc_id="yi_bond",
               queries=["票面利率", "债券代码"])["data"]
    assert [r["query"] for r in data["query_results"]] == ["票面利率", "债券代码"]


@pytest.mark.parametrize(
    ("answer_format", "answers", "expected"),
    [("multi", ["C、A"], ["AC"]), ("tf", ["正确"], ["A"]), ("pct", ["12.4％"], ["12.40%"])],
)
def test_finalize_normalizes_answers(make_registry, answer_format, answers, expected):
    registry = make_registry({"question": "q", "answer_format": answer_format})
    run(registry, "search_docs", query="甲公司")
    result = run(registry, "finalize", answers=answers)
    assert result["data"]["submitted"] == expected
    # 结束后不能再调用工具
    assert run(registry, "search_docs", query="甲公司")["error"] == "episode_terminated"
