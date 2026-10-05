"""对账 requeue_lost 的集成测试（真实 PostgreSQL；队列状态作为参数传入，不连 RabbitMQ）。

场景都是“消息已投递（outbox 是 sent），attempt 却还是 pending”，区别在于能不能断定消息丢了。
默认不跑；运行：conda run -n margin pytest -m integration
"""

import pytest
from sqlalchemy import select, text

from margin import lease, runs
from margin.dispatcher import QueueState, requeue_lost
from margin.models import Attempt, Outbox

pytestmark = pytest.mark.integration

OWNER = 1
BUSY_QUEUE = QueueState(ready=5, consumers=1)  # 队列里还有消息在排队：规则②不成立


def submit(db, key):
    runs.create_run(db, OWNER, key, "甲公司2023年营业收入是多少亿元？", None, "num", "DeepSeek")


def mark_sent(db, attempt_id, minutes_ago):
    """模拟 dispatcher 在若干分钟前已经把这个 attempt 投递出去。"""
    with db.begin() as conn:
        conn.execute(text("UPDATE outbox SET status = 'sent',"
                          " sent_at = now() - :m * interval '1 minute'"
                          " WHERE attempt_id = :a"), {"m": minutes_ago, "a": attempt_id})


def outbox_of(db, attempt_id):
    with db.connect() as conn:
        return conn.execute(select(Outbox).where(Outbox.attempt_id == attempt_id)).one()


def test_later_message_already_claimed_means_mine_was_lost(db):
    """水位线：比我晚投递的 attempt 2 已被领取，我（attempt 1）还在 pending 且过了宽限期 → 补发。"""
    submit(db, "k1")
    submit(db, "k2")
    mark_sent(db, 1, minutes_ago=5)
    mark_sent(db, 2, minutes_ago=4)
    lease.claim(db, 2, "worker-a")

    assert requeue_lost(db, BUSY_QUEUE) == ([1], [])
    row = outbox_of(db, 1)
    assert (row.status, row.redeliveries) == ("pending", 1)


def test_no_requeue_within_grace_period(db):
    """1 分钟前才投递：即使水位线已越过也不补发——几个子进程同时取消息，领取顺序本来就有抖动。"""
    submit(db, "k1")
    submit(db, "k2")
    mark_sent(db, 1, minutes_ago=1)
    mark_sent(db, 2, minutes_ago=0.5)
    lease.claim(db, 2, "worker-a")

    assert requeue_lost(db, BUSY_QUEUE) == ([], [])
    assert outbox_of(db, 1).status == "sent"


def test_empty_queue_with_pending_attempt_means_lost(db):
    """没有流量时水位线不动：队列里一条就绪消息都没有，我却还没被领取 → 补发。"""
    submit(db, "k1")
    mark_sent(db, 1, minutes_ago=3)

    assert requeue_lost(db, QueueState(ready=0, consumers=1)) == ([1], [])
    assert outbox_of(db, 1).status == "pending"


def test_normal_backlog_is_not_requeued(db):
    """积压时正常排队：队列不空、后面的消息没被领走、不到 30 分钟 → 不补发，否则积压时越补越多。"""
    submit(db, "k1")
    submit(db, "k2")
    mark_sent(db, 1, minutes_ago=10)
    mark_sent(db, 2, minutes_ago=9)

    assert requeue_lost(db, BUSY_QUEUE) == ([], [])
    assert (outbox_of(db, 1).status, outbox_of(db, 2).status) == ("sent", "sent")


def test_no_requeue_when_no_worker_is_consuming(db):
    """消费者数为 0（worker 全挂了）：补发也没人收，不补发，交给告警。"""
    submit(db, "k1")
    mark_sent(db, 1, minutes_ago=40)

    assert requeue_lost(db, QueueState(ready=0, consumers=0)) == ([], [])
    assert outbox_of(db, 1).redeliveries == 0


def test_gives_up_after_three_redeliveries_and_allows_regenerate(db):
    """补发 3 次仍判定丢失：attempt 判为 delivery_lost 失败、epoch+1，页面能看到失败原因；
    之后用户点“重新生成”可以正常建新的执行（不再被“还有执行没结束”挡住）。"""
    submit(db, "k1")
    mark_sent(db, 1, minutes_ago=3)
    with db.begin() as conn:
        conn.execute(text("UPDATE outbox SET redeliveries = 3 WHERE attempt_id = 1"))

    assert requeue_lost(db, QueueState(ready=0, consumers=1)) == ([], [1])
    with db.connect() as conn:
        attempt = conn.execute(select(Attempt).where(Attempt.id == 1)).one()
    assert (attempt.status, attempt.error, attempt.lease_epoch) == ("failed", "delivery_lost", 1)
    events, status = runs.events_after(db, OWNER, 1, after_seq=0)
    assert status == "failed"
    assert (events[-1].type, events[-1].payload["error"]) == ("attempt_failed", "delivery_lost")
    assert runs.regenerate(db, OWNER, 1) == 2


def test_attempt_failed_as_undelivered_does_not_raise_the_watermark(db):
    """判为 delivery_lost 的 attempt 从来没被领取过，不能算进水位线：
    否则排在它前面、正常排队的任务会被误判丢失，白白耗掉补发次数。"""
    submit(db, "k1")
    submit(db, "k2")
    mark_sent(db, 1, minutes_ago=5)
    mark_sent(db, 2, minutes_ago=4)
    with db.begin() as conn:
        conn.execute(text("UPDATE attempts SET status = 'failed', error = 'delivery_lost'"
                          " WHERE id = 2"))

    assert requeue_lost(db, BUSY_QUEUE) == ([], [])
