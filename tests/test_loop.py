"""主循环测试：RC6-C 的笔记压缩、思考回灌和各种停止条件。"""

import pytest
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


def test_stream_deltas_are_tagged_with_turn(make_registry):
    """流式模式下，每个思考片段带上轮次，前端据此把它放进对应的步骤卡片。"""
    llm = FakeLLM([
        call("search_docs", reasoning="先检索", query="甲公司"),
        call("finalize", reasoning="可以提交了", answers=["120.5"]),
    ])
    deltas = []
    run_episode(TASK, llm, make_registry(TASK), on_delta=lambda *d: deltas.append(d))

    assert deltas == [(0, "reasoning", "先检索"), (1, "reasoning", "可以提交了")]


def test_on_step_receives_each_step_as_it_completes(make_registry):
    """worker 靠 on_step 逐步落库：每一步工具执行完立刻回调，且带上这一轮的 token 用量。"""
    script = [
        call("search_docs", query="甲公司 营业收入"),
        call("finalize", answers=["120.5"]),
    ]
    script[0].usage = {"prompt_tokens": 900, "completion_tokens": 40}
    llm = FakeLLM(script)
    seen = []
    run_episode(TASK, llm, make_registry(TASK), on_step=lambda step: seen.append(
        (step.turn, step.tool_name, len(llm.requests), step.usage)))

    # 第 0 步回调时模型只被调用了 1 次：说明是“做完一步就回调”，不是全部结束后才补
    assert seen == [
        (0, "search_docs", 1, {"prompt_tokens": 900, "completion_tokens": 40}),
        (1, "finalize", 2, {}),
    ]


def test_on_step_error_stops_episode_without_more_model_calls(make_registry):
    """执行权被别人取代时，落库回调会抛异常：本题必须立刻中断，不能继续调用模型（花钱、写脏数据）。"""
    llm = FakeLLM([
        call("search_docs", query="甲公司"),
        call("finalize", answers=["120.5"]),
    ])

    def lose_lease(step):
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError):
        run_episode(TASK, llm, make_registry(TASK), on_step=lose_lease)
    assert len(llm.requests) == 1
