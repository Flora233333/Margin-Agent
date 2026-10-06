"""worker 执行、dispatcher 投递与唤醒的集成测试（真实 PostgreSQL；FakeLLM；不连 RabbitMQ）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

import json
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from fakes import FakeLLM, call
from sqlalchemy import select
from sqlalchemy.orm import Session

from margin import live, runs
from margin.dispatcher import (
    MAX_WAIT_SECONDS,
    dispatch_once,
    listen,
    make_publisher,
    wait_for_notify,
)
from margin.live import make_redis
from margin.models import Outbox, Run
from margin.worker import execute

pytestmark = pytest.mark.integration

OWNER = 1


def submit(db, key="k1", answer_format="num", require_citation=False):
    """require_citation 默认关：多数测试的假模型不引用，和引用提醒无关。"""
    return runs.create_run(db, OWNER, key, "甲公司2023年营业收入是多少亿元？", None,
                           answer_format, "DeepSeek", require_citation=require_citation)[0]


UNDERSTOOD = ('{"title": "甲公司 2023 年营业收入", "label": "甲公司 · 2023 年营业收入（亿元）", '
              '"answer_format": "num"}')


def gateway_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://gateway/v1/chat/completions")
    return httpx.HTTPStatusError(str(status), request=request,
                                 response=httpx.Response(status, request=request))


class BrokenGateway:
    """第一轮正常，第二轮网关返回 502。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, max_tokens, tool_choice, on_delta=None):
        self.calls += 1
        if self.calls == 1:
            return call("search_docs", query="甲公司 营业收入")
        raise gateway_error(502)


def test_worker_runs_attempt_and_persists_every_step(db, corpus, retriever, live_redis):
    """提交 -> worker 执行 -> 每一步和最终答案都在库里，刷新页面能看到完整过程。"""
    run_id = submit(db)
    llm = FakeLLM([
        call("search_docs", reasoning="先找年报", query="甲公司 2023 营业收入"),
        call("read_section", doc_id="jia_2023", block_id="jia_2023_b0001"),
        call("finalize", answers=["120.5"]),
    ])

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    attempt = detail["attempts"][0]
    assert (detail["status"], attempt["status"], attempt["violation"]) == (
        "completed", "completed", None)
    assert attempt["final"]["submitted"] == ["120.50"]
    assert [s["tool_name"] for s in attempt["steps"]] == [
        "search_docs", "read_section", "finalize"]
    assert attempt["steps"][0]["reasoning"] == "先找年报"


def test_step_events_carry_citation_numbers(db, corpus, retriever, live_redis):
    """引用编号由 worker 随 step 事件下发。

    前端来源卡片和以后的撰写回答都按这个编号，不再各算各的。
    """
    run_id = submit(db)
    quote = {"doc_id": "jia_2023", "block_id": "jia_2023_b0001"}
    llm = FakeLLM([
        call("search_docs", query="甲公司 2023 营业收入"),
        call("read_section", **quote),
        call("cite", quote="实现营业收入120.5亿元", **quote),
        call("cite", quote="实现营业收入120.5亿元", **quote),  # 重复引用同一处
        call("cite", quote="同比增长12.4%", **quote),
        call("finalize", answers=["120.5"]),
    ])

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    events, _ = runs.events_after(db, OWNER, run_id, 0)
    steps = [e.payload for e in events if e.type == "step"]
    assert [(s["tool_name"], s["citation_no"]) for s in steps] == [
        ("search_docs", None), ("read_section", None),
        ("cite", 1), ("cite", None), ("cite", 2), ("finalize", None)]


def test_answer_format_comes_from_understanding_when_not_submitted(db, corpus, retriever,
                                                                  live_redis):
    """产品里提交不带答案格式：理解题目判断为 num，finalize 按 num 规范化（120.5 -> 120.50）；
    标题写进库，run_understood 事件排在 attempt_finished 前面（刷新页面先看到标题）。"""
    run_id = submit(db, answer_format=None)
    llm = FakeLLM([call("search_docs", query="甲公司 2023 营业收入"),
                   call("finalize", answers=["120.5"])], understand=UNDERSTOOD)

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    assert detail["attempts"][0]["final"]["submitted"] == ["120.50"]
    assert (detail["title"], detail["guessed_format"]) == ("甲公司 2023 年营业收入", "num")
    assert detail["answer_format"] is None  # 猜的格式不写进“给定的格式”，见迁移 0006
    events, _ = runs.events_after(db, OWNER, run_id, 0)
    types = [e.type for e in events]
    assert types.index("run_understood") < types.index("attempt_finished")


def test_answer_not_matching_guessed_format_is_accepted_as_text(db, corpus, retriever, live_redis):
    """理解题目把“有什么时间要求”猜成了日期，模型交的是规定原文：按文本收下，不退回。
    退回的话模型看不到格式，只能猜系统要什么，会把对的答案改成编出来的日期（run 66）。
    重新生成时用库里存的猜测，同样不硬卡。"""
    run_id = submit(db, answer_format=None)
    reply = "自施行之日起6个月内完成较高风险客户，2年内完成全部存量客户"
    guessed_date = '{"title": "存量客户识别时限", "answer_format": "date"}'
    first = FakeLLM([call("search_docs", query="存量客户 受益所有人 时间"),
                     call("finalize", answers=[reply])], understand=guessed_date)
    execute(db, corpus, retriever, lambda model: first, live_redis, 1, "worker-a")
    runs.regenerate(db, OWNER, run_id)
    second = FakeLLM([call("search_docs", query="存量客户 受益所有人 时间"),
                      call("finalize", answers=[reply])])
    execute(db, corpus, retriever, lambda model: second, live_redis, 2, "worker-a")

    for attempt in runs.get_run(db, OWNER, run_id)["attempts"]:
        assert [s["tool_name"] for s in attempt["steps"]] == ["search_docs", "finalize"]
        assert attempt["final"]["raw"] == [reply]  # 一次就收下（文本规范化会把全角逗号换成半角）
        assert attempt["final"]["answer_format_fallback"] == "date"


def test_failed_understanding_does_not_fail_the_run(db, corpus, retriever, live_redis):
    """理解题目时网关报错：这道题照常答完，标题退回问题原句的前 20 个字，格式按文本。"""
    run_id = submit(db, answer_format=None)
    llm = FakeLLM([call("search_docs", query="甲公司 2023 营业收入"),
                   call("finalize", answers=["120.5亿元"])], understand=TimeoutError())

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    assert (detail["status"], detail["guessed_format"]) == ("completed", "text")
    assert detail["title"] == "甲公司2023年营业收入是多少亿元？"[:20]


def test_regenerate_does_not_understand_the_question_again(db, corpus, retriever, live_redis):
    """重新生成：标题和格式第一次已经有了，不再多花一次模型调用。"""
    run_id = submit(db, answer_format=None)
    first = FakeLLM([call("search_docs", query="甲公司 营业收入"),
                     call("finalize", answers=["120.5"])], understand=UNDERSTOOD)
    execute(db, corpus, retriever, lambda model: first, live_redis, 1, "worker-a")
    runs.regenerate(db, OWNER, run_id)
    second = FakeLLM([call("search_docs", query="甲公司 营业收入"),
                      call("finalize", answers=["120.5"])])

    execute(db, corpus, retriever, lambda model: second, live_redis, 2, "worker-a")

    assert "understand" not in second.completions
    detail = runs.get_run(db, OWNER, run_id)
    assert detail["attempts"][1]["final"]["submitted"] == ["120.50"]


def test_product_run_without_citation_is_reminded_and_marked_uncited(db, corpus, retriever,
                                                                     live_redis):
    """产品题（提交时默认打开引用提醒）：开关从 runs 一路带到 finalize，
    模型不引用就交会被提醒一次，第二次照收，答案标 uncited。"""
    run_id = submit(db, require_citation=True)
    llm = FakeLLM([call("search_docs", query="甲公司 2023 营业收入"),
                   call("finalize", answers=["120.5"]),
                   call("finalize", answers=["120.5"])])

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    attempt = runs.get_run(db, OWNER, run_id)["attempts"][0]
    assert [s["result"].get("error") for s in attempt["steps"]] == [
        None, "citation_required", None]
    assert (attempt["status"], attempt["final"]["uncited"]) == ("completed", True)


def test_written_answer_is_checked_then_stored(db, corpus, retriever, live_redis):
    """撰写的回答：模型看到的引用编号和右侧卡片一致（重复引用不另编号），
    写错的 [n] 在进库前删掉；answer_written 事件排在 attempt_finished 前面。"""
    run_id = submit(db)
    quote = {"doc_id": "jia_2023", "block_id": "jia_2023_b0001"}
    llm = FakeLLM([
        call("search_docs", query="甲公司 2023 营业收入"),
        call("read_section", **quote),
        call("cite", quote="实现营业收入120.5亿元", **quote),
        call("cite", quote="实现营业收入120.5亿元", **quote),
        call("cite", quote="同比增长12.4%", **quote),
        call("finalize", answers=["120.5"]),
    ], compose="年报披露营业收入120.5亿元[1]，同比增长12.4%[2][3]。")

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    assert "[2] jia_2023 · jia_2023_b0001：…" in llm.last_input
    assert "[3]" not in llm.last_input
    written = runs.get_run(db, OWNER, run_id)["attempts"][0]["final"]["written"]
    assert written == {"text": "年报披露营业收入120.5亿元[1]，同比增长12.4%[2]。",
                       "citations": [1, 2], "unverified": []}
    events, _ = runs.events_after(db, OWNER, run_id, 0)
    assert [e.type for e in events][-2:] == ["answer_written", "attempt_finished"]


def test_failed_compose_still_completes_with_short_answer(db, corpus, retriever, live_redis):
    """撰写回答时网关报错：这道题照常完成，页面退回短答案；记下的错误不带网关地址。"""
    run_id = submit(db)
    llm = FakeLLM([call("search_docs", query="甲公司 2023 营业收入"),
                   call("finalize", answers=["120.5"])], compose=gateway_error(502))

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    final = detail["attempts"][0]["final"]
    assert (detail["status"], final["submitted"]) == ("completed", ["120.50"])
    assert final["written"] == {"error": "llm_http_502"}


def test_gateway_error_fails_attempt_but_keeps_finished_steps(db, corpus, retriever, live_redis):
    """网关中途报错：这次执行判为失败（用户可重新生成），已完成的步骤保留，错误信息不带网关地址。"""
    run_id = submit(db)

    execute(db, corpus, retriever, lambda model: BrokenGateway(), live_redis, 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    attempt = detail["attempts"][0]
    assert (detail["status"], attempt["error"]) == ("failed", "llm_http_502")
    assert len(attempt["steps"]) == 1


def test_duplicate_delivery_does_not_call_the_model_again(db, corpus, retriever, live_redis):
    """同一个 attempt 的第二条消息：领取失败，直接跳过，不会再调用模型。"""
    submit(db)
    execute(db, corpus, retriever, lambda model: FakeLLM([call("finalize", answers=["1"])]),
            live_redis, 1, "worker-a")
    second = FakeLLM([])

    execute(db, corpus, retriever, lambda model: second, live_redis, 1, "worker-b")
    assert second.requests == []


def test_dispatcher_publishes_each_pending_attempt_once(db):
    submit(db, "k1")
    submit(db, "k2")
    published = []

    assert dispatch_once(db, published.append) == 2
    assert dispatch_once(db, published.append) == 0
    assert published == [1, 2]


def test_broker_down_keeps_outbox_pending_and_backs_off(db):
    """RabbitMQ 连不上：记录保持 pending、记下失败次数并推迟重试，不会每秒疯狂重发。

    用真实的 make_publisher 连一个没人监听的端口，确认真正抛出的异常能被 dispatch_once 接住
    （拒收、等确认超时抛的是同一个异常，实测记录在 make_publisher 的注释里）。
    """
    submit(db)

    assert dispatch_once(db, make_publisher("amqp://margin:x@127.0.0.1:1//")) == 0
    with db.connect() as conn:
        row = conn.execute(select(Outbox)).one()
    assert (row.status, row.tries, row.sent_at) == ("pending", 1, None)
    assert row.next_attempt_at > datetime.now(UTC)
    assert dispatch_once(db, lambda attempt_id: None) == 0  # 还没到重试时间


# ---- LISTEN / NOTIFY 唤醒 ----

@pytest.fixture
def listener(db, database_url):
    """dispatcher 的专用 LISTEN 连接，连测试库。"""
    conn = listen(database_url)
    yield conn
    conn.close()


def test_new_submission_wakes_dispatcher_within_one_second(db, listener):
    """提交后 dispatcher 立刻被通知叫醒并投递，不用等 10 秒一次的兜底扫描。"""
    threading.Timer(0.2, submit, args=[db]).start()  # 0.2 秒后另一个线程提交
    start = time.monotonic()

    assert wait_for_notify(listener, timeout=MAX_WAIT_SECONDS)
    assert time.monotonic() - start < 1
    published = []
    assert dispatch_once(db, published.append) == 1


def test_rolled_back_submission_does_not_wake_dispatcher(db, listener):
    """建任务的事务中途出错回滚：通知随事务一起取消，dispatcher 不会醒，也没有东西可投递。"""
    with pytest.raises(RuntimeError), Session(db) as session, session.begin():
        run = Run(owner_id=OWNER, idempotency_key="k1", question="甲公司营业收入？",
                  answer_format="num", model="DeepSeek")
        session.add(run)
        session.flush()
        runs._enqueue_attempt(session, run.id, 1, "submit", "DeepSeek")
        raise RuntimeError("写完 outbox 之后出错")

    assert not wait_for_notify(listener, timeout=1)
    assert dispatch_once(db, lambda attempt_id: None) == 0


# ---- 实时片段（Redis pub/sub）----

def test_worker_publishes_thinking_tagged_with_attempt_and_epoch(db, corpus, retriever,
                                                                live_redis):
    """思考片段和回答正文带着 attempt_id、epoch、轮次发到这个 run 的频道：
    前端靠前两个丢弃旧执行残留的片段；回答正文排在最后一步之后。"""
    run_id = submit(db)
    pubsub = live_redis.pubsub()
    pubsub.subscribe(live.channel(run_id))
    pubsub.get_message(timeout=1)  # 第一条是“订阅成功”的确认
    llm = FakeLLM([call("search_docs", reasoning="先找年报", query="甲公司 营业收入"),
                   call("finalize", reasoning="可以提交了", answers=["120.5"])],
                  compose="营业收入为120.5亿元。")

    execute(db, corpus, retriever, lambda model: llm, live_redis, 1, "worker-a")

    messages = []
    while message := pubsub.get_message(timeout=1):
        messages.append(json.loads(message["data"]))
    pubsub.close()
    assert [(m["turn"], m["kind"], m["text"]) for m in messages] == [
        (0, "reasoning", "先找年报"), (1, "reasoning", "可以提交了"),
        (2, "answer", "营业收入为120.5亿元。")]
    assert {(m["attempt_id"], m["epoch"]) for m in messages} == {(1, 1)}


def test_redis_down_does_not_fail_the_attempt(db, corpus, retriever):
    """Redis 挂了：实时片段发不出去，但这道题照常执行完、每一步照常落库（持久事件不依赖 Redis）。"""
    run_id = submit(db)
    llm = FakeLLM([call("search_docs", reasoning="先找年报", query="甲公司 营业收入"),
                   call("finalize", reasoning="可以提交了", answers=["120.5"])])

    execute(db, corpus, retriever, lambda model: llm, make_redis("redis://127.0.0.1:1/0"), 1,
            "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    assert (detail["status"], len(detail["attempts"][0]["steps"])) == ("completed", 2)
