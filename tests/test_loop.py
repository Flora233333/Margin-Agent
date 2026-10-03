"""主循环测试：RC6-C 的笔记压缩、思考回灌和各种停止条件。"""

from fakes import FakeLLM, call, say

from margin.harness import run_episode
from margin.harness.prompt import PROTOCOL_REPAIR_MESSAGE

TASK = {"question": "甲公司2023年营业收入是多少亿元？", "answer_format": "num"}


def test_happy_path_search_read_note_finalize(make_registry):
    llm = FakeLLM([
        call("search_docs", reasoning="先找甲公司2023年报", query="甲公司 2023 营业收入"),
        call("read_section", doc_id="jia_2023", block_id="jia_2023_b0001"),
        call("write_note", note="营业收入120.5亿元，来源 jia_2023_b0001"),
        call("finalize", answers=["120.5"]),
    ])
    trace = run_episode(TASK, llm, make_registry(TASK))

    assert trace.violation is None
    assert trace.final["submitted"] == ["120.50"]
    assert [s.tool_name for s in trace.steps] == [
        "search_docs", "read_section", "write_note", "finalize"]


def test_write_note_compacts_context(make_registry):
    """写笔记成功后，下一次请求只剩 [system, 题目, 笔记]，之前的检索和原文都被丢掉。"""
    llm = FakeLLM([
        call("search_docs", query="甲公司 营业收入"),
        call("write_note", note="已找到 jia_2023"),
        call("finalize", answers=["120.5"]),
    ])
    run_episode(TASK, llm, make_registry(TASK))

    after_note = llm.requests[2]["messages"]
    assert [m["role"] for m in after_note] == ["system", "user", "user"]
    assert after_note[2]["content"] == "工作笔记 revision=1：\n已找到 jia_2023"


def test_reasoning_is_replayed_in_history(make_registry):
    """上一轮的思考作为 reasoning_content 出现在下一轮请求里。"""
    llm = FakeLLM([
        call("search_docs", reasoning="我先检索", query="甲公司"),
        call("finalize", answers=["1"]),
    ])
    run_episode(TASK, llm, make_registry(TASK))

    assistant = llm.requests[1]["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "我先检索"


def test_no_tool_call_gets_one_repair_then_stops(make_registry):
    llm = FakeLLM([say("答案是120.5"), say("还是120.5")])
    trace = run_episode(TASK, llm, make_registry(TASK))

    # 第一次没调工具：追加提醒，并强制下一轮调用工具
    assert llm.requests[1]["messages"][-1]["content"] == PROTOCOL_REPAIR_MESSAGE
    assert llm.requests[1]["tool_choice"] == "required"
    assert trace.violation == "provider_protocol_retry_exhausted"


def test_same_call_same_result_three_times_stops(make_registry):
    llm = FakeLLM([call("search_docs", query="甲公司")] * 3)
    trace = run_episode(TASK, llm, make_registry(TASK))
    assert trace.violation == "duplicate_no_progress"


def test_same_error_three_times_stops(make_registry):
    """同一个错误调用穿插在其他调用之间反复出现，累计 3 次也会停。
    （连续 3 次完全相同的调用会先被 duplicate_no_progress 拦下，见上一个测试。）"""
    bad = call("read_section", doc_id="jia_2023", block_id="jia_2023_b0001")  # 未搜索就读
    llm = FakeLLM([bad, call("compute", expression="1+1"), bad,
                   call("compute", expression="2+2"), bad])
    trace = run_episode(TASK, llm, make_registry(TASK))
    assert trace.violation == "repeated_tool_error"


def test_finalize_format_error_is_retryable(make_registry):
    """答案格式不对时模型可以重交，不会被当成原地打转。"""
    llm = FakeLLM([
        call("search_docs", query="甲公司"),
        call("finalize", answers=["约120亿"]),
        call("finalize", answers=["约120亿"]),
        call("finalize", answers=["约120亿"]),
        call("finalize", answers=["120.5"]),
    ])
    trace = run_episode(TASK, llm, make_registry(TASK))
    assert trace.violation is None
    assert trace.final["submitted"] == ["120.50"]


def test_turn_budget_excludes_write_note(make_registry):
    """max_turns 只计非 write_note 的调用。"""
    llm = FakeLLM([
        call("search_docs", query="甲公司"),
        call("write_note", note="笔记"),
        call("search_docs", query="甲公司 2022"),
    ])
    trace = run_episode(TASK, llm, make_registry(TASK), max_turns=2)
    assert len(trace.steps) == 3
    assert trace.violation == "max_turns"
