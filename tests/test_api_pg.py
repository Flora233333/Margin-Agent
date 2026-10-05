"""接口测试：通过 HTTP 调 FastAPI，背后连真实 PostgreSQL。

默认不跑；运行：conda run -n margin pytest -m integration
TestClient 在进程内直接调用 app，不用真的起服务器。
"""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from margin import lease, live
from margin.api import app, current_user, get_db, get_hub, get_redis_url
from margin.harness import Step

pytestmark = pytest.mark.integration

QUESTION = {"question": "甲公司2023年营业收入是多少亿元？", "answer_format": "num"}


@pytest.fixture
def client(db, hub):
    app.dependency_overrides[get_db] = lambda: db  # 接口改用测试库
    app.dependency_overrides[get_hub] = lambda: hub  # 事件通知也监听测试库
    yield TestClient(app)
    app.dependency_overrides.clear()


def submit(client, key="k1", body=QUESTION):
    return client.post("/runs", json=body, headers={"Idempotency-Key": key})


def test_resubmitting_with_same_key_returns_the_same_run(client):
    """前端超时重试：两次都返回 202 和同一个 run_id，后台只有一次执行在排队。"""
    first, second = submit(client), submit(client)

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json() == second.json() == {"run_id": 1}
    detail = client.get("/runs/1").json()
    assert detail["status"] == "queued"
    assert [(a["attempt_no"], a["status"]) for a in detail["attempts"]] == [(1, "pending")]


def test_same_key_with_different_question_returns_409(client):
    submit(client)
    response = submit(client, body={**QUESTION, "question": "乙公司的票面利率是多少？"})
    assert response.status_code == 409


@pytest.mark.parametrize(("headers", "body"), [
    ({}, QUESTION),  # 没带幂等键
    ({"Idempotency-Key": "k1"}, {**QUESTION, "answer_format": "essay"}),  # 不认识的答案类型
    ({"Idempotency-Key": "k1"}, {**QUESTION, "question": ""}),  # 空题目
    ({"Idempotency-Key": "k1"}, {**QUESTION, "options": {"E": "多出来的选项"}}),
])
def test_invalid_submission_is_rejected_without_creating_a_run(client, headers, body):
    assert client.post("/runs", json=body, headers=headers).status_code == 422
    assert client.get("/runs/1").status_code == 404


def test_other_users_run_returns_404(client):
    submit(client)
    app.dependency_overrides[current_user] = lambda: 999
    assert client.get("/runs/1").status_code == 404
    assert client.get("/runs/1/events").status_code == 404
    assert client.post("/runs/1/regenerate").status_code == 404


def test_regenerate_while_attempt_is_pending_returns_409(client):
    submit(client)
    assert client.post("/runs/1/regenerate").status_code == 409


def test_sse_resumes_after_last_event_id_and_closes_when_finished(client, db):
    """断线重连带 Last-Event-ID：只补发之后的事件；执行结束后服务端关闭流。"""
    submit(client)
    held = lease.claim(db, 1, "worker-a")
    step = Step(0, "先检索", "search_docs", '{"query": "甲公司"}', {"ok": True, "data": {}}, 1.0)
    lease.commit_step(db, held, step)
    lease.finish(db, held, {"name": "finalize", "submitted": ["120.50"]}, None)

    response = client.get("/runs/1/events", headers={"Last-Event-ID": "2"})

    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in response.text.split("\n\n") if b]
    assert [b.splitlines()[:2] for b in blocks] == [
        ["id: 3", "event: step"], ["id: 4", "event: attempt_finished"]]
    assert '"submitted": ["120.50"]' in blocks[1]


def test_sse_pushes_new_events_on_notify_without_waiting_for_fallback(client, db):
    """执行中有新步骤提交：SSE 被 PG 通知叫醒立刻推出去，不用等 10 秒一次的兜底查询；结束后关闭。"""
    submit(client)
    held = lease.claim(db, 1, "worker-a")
    step = Step(0, "先检索", "search_docs", '{"query": "甲公司"}', {"ok": True, "data": {}}, 1.0)

    def worker_progress():  # 模拟 worker：连接建立后 0.5 秒提交一步，再过 0.5 秒结束
        time.sleep(0.5)
        lease.commit_step(db, held, step)
        time.sleep(0.5)
        lease.finish(db, held, {"name": "finalize", "submitted": ["120.50"]}, None)

    threading.Thread(target=worker_progress).start()
    start = time.monotonic()
    response = client.get("/runs/1/events")

    assert time.monotonic() - start < 3  # 靠兜底的话至少要 10 秒
    types = [b.splitlines()[1] for b in response.text.split("\n\n") if b.startswith("id:")]
    assert types == ["event: attempt_queued", "event: attempt_started", "event: step",
                     "event: attempt_finished"]


def test_sse_forwards_live_thinking_without_event_id(client, db, live_redis):
    """执行中的思考片段经 Redis 转发为 event: delta，且不带 id：
    断线重连的 Last-Event-ID 只跟持久事件走，
    否则浏览器会拿片段的位置去补发，跳过或重复持久事件。"""
    submit(client)
    held = lease.claim(db, 1, "worker-a")
    delta = {"attempt_id": 1, "epoch": held.epoch, "turn": 0, "kind": "reasoning", "text": "先找"}

    def worker_progress():
        time.sleep(0.5)
        live_redis.publish(live.channel(1), json.dumps(delta, ensure_ascii=False))
        time.sleep(0.3)
        lease.finish(db, held, {"name": "finalize", "submitted": ["120.50"]}, None)

    threading.Thread(target=worker_progress).start()
    blocks = [b for b in client.get("/runs/1/events").text.split("\n\n") if not b.startswith(":")]

    assert [b.splitlines()[0] for b in blocks if b] == [
        "id: 1", "id: 2", "event: delta", "id: 3"]
    delta_block = next(b for b in blocks if b.startswith("event: delta"))
    assert json.loads(delta_block.splitlines()[1].removeprefix("data: ")) == delta


def test_sse_still_streams_persistent_events_when_redis_is_down(client, db):
    """Redis 挂了：拿不到实时片段，但持久事件照常推送，页面不会白屏或报错。"""
    app.dependency_overrides[get_redis_url] = lambda: "redis://127.0.0.1:1/0"
    submit(client)
    held = lease.claim(db, 1, "worker-a")
    lease.finish(db, held, {"name": "finalize", "submitted": ["120.50"]}, None)

    response = client.get("/runs/1/events")

    assert response.status_code == 200
    assert [b.splitlines()[1] for b in response.text.split("\n\n") if b] == [
        "event: attempt_queued", "event: attempt_started", "event: attempt_finished"]
