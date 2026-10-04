"""任务创建、幂等、租约与 epoch 的集成测试（真实 PostgreSQL）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

import pytest
from sqlalchemy import func, select, text, update

from margin import lease, runs
from margin.harness import Step
from margin.models import Attempt, Event, Outbox, Run
from margin.models import Step as StepRow

pytestmark = pytest.mark.integration

OWNER = 1  # 迁移预置的开发用户


def submit(db, key="k1", question="甲公司2023年营业收入是多少亿元？"):
    return runs.create_run(db, OWNER, key, question, None, "num", "DeepSeek")


def count(db, model) -> int:
    with db.connect() as conn:
        return conn.scalar(select(func.count()).select_from(model))


def make_step(turn=0) -> Step:
    return Step(turn, "先检索", "search_docs", '{"query": "甲公司"}', {"ok": True, "data": {}},
                1.5, {"prompt_tokens": 900, "completion_tokens": 40})


def test_same_idempotency_key_creates_only_one_run(db):
    """网络超时后前端重试、用户连点两下：同一个幂等键只能建一个 run、投递一次。"""
    first = submit(db)
    second = submit(db)

    assert first == (first[0], True)
    assert second == (first[0], False)
    assert (count(db, Run), count(db, Attempt), count(db, Outbox)) == (1, 1, 1)


def test_same_idempotency_key_with_different_question_is_rejected(db):
    """幂等键相同但内容不同，多半是前端 bug：拒绝，而不是悄悄返回旧题的结果。"""
    submit(db)
    with pytest.raises(runs.IdempotencyConflict):
        submit(db, question="乙公司的票面利率是多少？")


def test_duplicate_delivery_is_executed_only_once(db):
    """同一个 attempt 被投递两次（outbox 重复投递），只有第一个 worker 能领到。"""
    submit(db)
    first = lease.claim(db, 1, "worker-a")
    second = lease.claim(db, 1, "worker-b")

    assert first.epoch == 1
    assert first.task == {"question": "甲公司2023年营业收入是多少亿元？", "options": None,
                          "answer_format": "num"}
    assert second is None


def test_step_from_replaced_worker_is_rejected_and_not_written(db):
    """执行权已被取代（epoch 变了）的 worker 提交步骤：整个事务回滚，步骤和事件都不留下。"""
    submit(db)
    old = lease.claim(db, 1, "worker-a")
    with db.begin() as conn:  # 模拟执行权被取代：epoch 已经前进
        conn.execute(update(Attempt).where(Attempt.id == 1).values(lease_epoch=2))
    events_before = count(db, Event)

    with pytest.raises(lease.LeaseLost):
        lease.commit_step(db, old, make_step())
    assert count(db, StepRow) == 0
    assert count(db, Event) == events_before


def test_expired_lease_fails_attempt_and_fences_the_old_worker(db):
    """worker 死了或卡住、不再续租：巡检把它判为失败；它就算醒来，写步骤、写结果都被拒绝。
    租约没过期的执行不受影响。"""
    submit(db, key="dead")
    submit(db, key="alive")
    dead = lease.claim(db, 1, "worker-a")
    alive = lease.claim(db, 2, "worker-b")
    with db.begin() as conn:  # 模拟 worker-a 已经 2 分钟没续租
        conn.execute(text("UPDATE attempts SET lease_until = now() - interval '2 minutes'"
                          " WHERE id = 1"))

    assert lease.expire_leases(db) == [1]
    with pytest.raises(lease.LeaseLost):
        lease.commit_step(db, dead, make_step())
    with pytest.raises(lease.LeaseLost):
        lease.finish(db, dead, {"name": "finalize"}, None)
    assert lease.renew(db, alive)
    detail = runs.get_run(db, OWNER, 1)
    assert (detail["status"], detail["attempts"][0]["error"]) == ("failed", "lease_expired")


def test_events_are_numbered_continuously_in_commit_order(db):
    """SSE 断线重连按 seq 补发：同一个 run 的事件序号必须从 1 开始连续、不重复、不跳号。"""
    submit(db)
    held = lease.claim(db, 1, "worker-a")
    lease.commit_step(db, held, make_step(0))
    lease.commit_step(db, held, make_step(1))
    lease.finish(db, held, {"name": "finalize", "submitted": ["120.50"]}, None)

    events, status = runs.events_after(db, 1, after_seq=0)
    assert [(e.seq, e.type) for e in events] == [
        (1, "attempt_queued"), (2, "attempt_started"), (3, "step"), (4, "step"),
        (5, "attempt_finished")]
    assert status == "completed"
    assert [e.seq for e in runs.events_after(db, 1, after_seq=3)[0]] == [4, 5]


def test_regenerate_is_rejected_while_an_attempt_is_still_active(db):
    """还在执行时又点“重新生成”：拒绝，否则同一道题会有两个执行同时跑。"""
    submit(db)
    with pytest.raises(runs.RunBusy):
        runs.regenerate(db, OWNER, 1)


def test_regenerate_after_finish_starts_new_attempt_and_keeps_history(db):
    """重新生成：新建第 2 次执行并投递；第 1 次的步骤和结果原样保留。"""
    submit(db)
    held = lease.claim(db, 1, "worker-a")
    lease.commit_step(db, held, make_step())
    lease.finish(db, held, {"name": "finalize", "submitted": ["120.50"]}, None)

    assert runs.regenerate(db, OWNER, 1) == 2
    detail = runs.get_run(db, OWNER, 1)
    assert detail["status"] == "queued"
    assert [(a["attempt_no"], a["trigger"], a["status"], len(a["steps"]))
            for a in detail["attempts"]] == [(1, "submit", "completed", 1),
                                             (2, "regenerate", "pending", 0)]
    assert count(db, Outbox) == 2


def test_other_users_run_is_not_visible(db):
    """访问别人的 run 和访问不存在的 run 一样，都是“找不到”。"""
    submit(db)
    with pytest.raises(runs.RunNotFound):
        runs.get_run(db, 999, 1)
