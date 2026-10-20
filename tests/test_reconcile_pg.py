"""重发 resend_lost 的集成测试（真实 PostgreSQL；队列状态作为参数传入，不连 RabbitMQ）。

场景都是“消息已发出（outbox 是 sent），attempt 却还是 pending”，区别在于发出多久了、能不能下判断。
M1.6 起只看时间：发出 1 分钟还没 claim 就重发，间隔 2、4 分钟，3 次后再过 8 分钟判失败（D28）。
默认不跑；运行：conda run -n margin pytest -m integration
"""

import pytest
from sqlalchemy import select, text

from margin import runs
from margin.dispatcher import QueueState, resend_lost
from margin.models import Attempt, Outbox

pytestmark = pytest.mark.integration

OWNER = 1
IDLE = QueueState(ready=0, consumers=1)  # 有 worker 在线、队列里没有排队的消息：可以判断


def submit(db, key="k1"):
    runs.create_run(db, OWNER, key, "甲公司2023年营业收入是多少亿元？", None, "num", "DeepSeek")


def mark_sent(db, attempt_id, minutes_ago, redeliveries=0):
    """模拟 dispatcher 在若干分钟前把这个 attempt 发了出去（已经重发过 redeliveries 次）。"""
    with db.begin() as conn:
        conn.execute(text("UPDATE outbox SET status = 'sent', redeliveries = :r,"
                          " sent_at = now() - :m * interval '1 minute'"
                          " WHERE attempt_id = :a"),
                     {"m": minutes_ago, "r": redeliveries, "a": attempt_id})


def outbox_of(db, attempt_id):
    with db.connect() as conn:
        return conn.execute(select(Outbox).where(Outbox.attempt_id == attempt_id)).one()


def test_unclaimed_one_minute_after_sending_is_resent(db):
    """发出满 1 分钟还没被 claim：worker 收到消息先 claim、又只在有空位时才发，
    正常情况下这段只有几毫秒，所以只能是丢了 → outbox 改回 pending 重发，重发次数 1。
    刚发出 30 秒的不动。"""
    submit(db, "k1")
    submit(db, "k2")
    mark_sent(db, 1, minutes_ago=1.1)
    mark_sent(db, 2, minutes_ago=0.5)

    assert resend_lost(db, IDLE) == ([1], [])
    row = outbox_of(db, 1)
    assert (row.status, row.redeliveries) == ("pending", 1)
    assert outbox_of(db, 2).status == "sent"


def test_resent_message_waits_twice_as_long_before_next_resend(db):
    """重发过 1 次：下一次要等发出满 2 分钟（退避），1.5 分钟时不动，2.5 分钟时才重发第 2 次。"""
    submit(db)
    mark_sent(db, 1, minutes_ago=1.5, redeliveries=1)
    assert resend_lost(db, IDLE) == ([], [])

    mark_sent(db, 1, minutes_ago=2.5, redeliveries=1)
    assert resend_lost(db, IDLE) == ([1], [])
    assert outbox_of(db, 1).redeliveries == 2


def test_messages_waiting_in_queue_pause_resending(db):
    """RabbitMQ 里还有就绪消息：说明消息其实在队列里排着（子进程数配错、worker 卡住……），
    这时按时间判会把排队的当成丢了，所以暂停重发，发出 10 分钟的也不动。"""
    submit(db)
    mark_sent(db, 1, minutes_ago=10)

    assert resend_lost(db, QueueState(ready=2, consumers=1)) == ([], [])
    assert (outbox_of(db, 1).status, outbox_of(db, 1).redeliveries) == ("sent", 0)


@pytest.mark.parametrize("queue", [None, QueueState(ready=0, consumers=0)],
                         ids=["读不到队列状态", "没有消费者"])
def test_no_resend_when_workers_cannot_be_seen(db, queue):
    """读不到 RabbitMQ 的队列状态，或者没有 worker 在线：重发了也判断不了 / 没人收，不重发，
    也不消耗重发次数（worker 全挂了交给告警，回来以后照常执行）。"""
    submit(db)
    mark_sent(db, 1, minutes_ago=40)

    assert resend_lost(db, queue) == ([], [])
    assert outbox_of(db, 1).redeliveries == 0


def test_gives_up_eight_minutes_after_third_resend_and_allows_regenerate(db):
    """重发 3 次后：再等 8 分钟（7 分钟时还不动），仍没被 claim 就判 delivery_lost：
    attempt 失败、epoch+1，页面能看到原因；之后用户点“重新生成”可以正常建新的执行。"""
    submit(db)
    mark_sent(db, 1, minutes_ago=7, redeliveries=3)
    assert resend_lost(db, IDLE) == ([], [])

    mark_sent(db, 1, minutes_ago=8.5, redeliveries=3)
    assert resend_lost(db, IDLE) == ([], [1])
    with db.connect() as conn:
        attempt = conn.execute(select(Attempt).where(Attempt.id == 1)).one()
    assert (attempt.status, attempt.error, attempt.lease_epoch) == ("failed", "delivery_lost", 1)
    events, status = runs.events_after(db, OWNER, 1, after_seq=0)
    assert status == "failed"
    assert (events[-1].type, events[-1].payload["error"]) == ("attempt_failed", "delivery_lost")
    assert runs.regenerate(db, OWNER, 1) == 2
