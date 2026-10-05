"""worker 执行与 dispatcher 投递的集成测试（真实 PostgreSQL；模型用 FakeLLM，不连 RabbitMQ）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

from datetime import UTC, datetime

import httpx
import pytest
from fakes import FakeLLM, call
from sqlalchemy import select

from margin import runs
from margin.dispatcher import dispatch_once, make_publisher
from margin.models import Outbox
from margin.worker import execute

pytestmark = pytest.mark.integration

OWNER = 1


def submit(db, key="k1"):
    return runs.create_run(db, OWNER, key, "甲公司2023年营业收入是多少亿元？", None, "num",
                           "DeepSeek")[0]


class BrokenGateway:
    """第一轮正常，第二轮网关返回 502。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, max_tokens, tool_choice, on_delta=None):
        self.calls += 1
        if self.calls == 1:
            return call("search_docs", query="甲公司 营业收入")
        request = httpx.Request("POST", "http://gateway/v1/chat/completions")
        raise httpx.HTTPStatusError("502", request=request,
                                    response=httpx.Response(502, request=request))


def test_worker_runs_attempt_and_persists_every_step(db, corpus, retriever):
    """提交 -> worker 执行 -> 每一步和最终答案都在库里，刷新页面能看到完整过程。"""
    run_id = submit(db)
    llm = FakeLLM([
        call("search_docs", reasoning="先找年报", query="甲公司 2023 营业收入"),
        call("read_section", doc_id="jia_2023", block_id="jia_2023_b0001"),
        call("finalize", answers=["120.5"]),
    ])

    execute(db, corpus, retriever, lambda model: llm, 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    attempt = detail["attempts"][0]
    assert (detail["status"], attempt["status"], attempt["violation"]) == (
        "completed", "completed", None)
    assert attempt["final"]["submitted"] == ["120.50"]
    assert [s["tool_name"] for s in attempt["steps"]] == [
        "search_docs", "read_section", "finalize"]
    assert attempt["steps"][0]["reasoning"] == "先找年报"


def test_gateway_error_fails_attempt_but_keeps_finished_steps(db, corpus, retriever):
    """网关中途报错：这次执行判为失败（用户可重新生成），已完成的步骤保留，错误信息不带网关地址。"""
    run_id = submit(db)

    execute(db, corpus, retriever, lambda model: BrokenGateway(), 1, "worker-a")

    detail = runs.get_run(db, OWNER, run_id)
    attempt = detail["attempts"][0]
    assert (detail["status"], attempt["error"]) == ("failed", "llm_http_502")
    assert len(attempt["steps"]) == 1


def test_duplicate_delivery_does_not_call_the_model_again(db, corpus, retriever):
    """同一个 attempt 的第二条消息：领取失败，直接跳过，不会再调用模型。"""
    submit(db)
    execute(db, corpus, retriever, lambda model: FakeLLM([call("finalize", answers=["1"])]),
            1, "worker-a")
    second = FakeLLM([])

    execute(db, corpus, retriever, lambda model: second, 1, "worker-b")
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
