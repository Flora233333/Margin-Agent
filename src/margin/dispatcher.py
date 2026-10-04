"""Dispatcher：把 outbox 里待投递的 attempt 发到 Celery 队列；顺带做租约巡检。

启动：python -m margin.dispatcher（compose 里的 dispatcher 服务）

为什么不在 API 里直接发 Celery 消息：见 runs._enqueue_attempt 的 Outbox 说明。
API 只负责把“要投递”写进 outbox 表（和建任务同一个事务），这里每秒扫一次，发出去后标记 sent。

投递语义是“至少一次”：消息发出去了、但标记 sent 的事务没提交成功（例如这时进程崩溃），
下一轮会再发一次。重复的消息没关系——worker 领取执行权时只有一个能成功（lease.claim）。

出错策略：发消息失败（Redis 连不上）是预期内的外部故障，记下失败次数、推迟下次投递时间；
数据库出错则让进程直接退出，由 compose 的 restart 策略重新拉起。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta

from kombu.exceptions import OperationalError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from . import lease
from .db import get_engine
from .models import Outbox
from .worker import celery_app

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0
INSPECT_EVERY_SECONDS = 15  # 每 15 秒巡检一次过期租约
BATCH = 50  # 一轮最多投递多少条
MAX_BACKOFF_SECONDS = 60


def dispatch_once(engine: Engine, publish: Callable[[int], None]) -> int:
    """投递一轮：取出到期的 pending 记录逐条发送，返回发送成功的条数。

    FOR UPDATE SKIP LOCKED：锁住取出的行，别的 dispatcher 进程跳过它们去取别的行。
    现在只有一个 dispatcher，但以后多开几个也不会把同一条记录发两次。
    """
    with Session(engine) as session, session.begin():
        rows = session.execute(
            select(Outbox)
            .where(Outbox.status == "pending", Outbox.next_attempt_at <= func.now())
            .order_by(Outbox.id).limit(BATCH)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        sent = 0
        for row in rows:
            try:
                publish(row.attempt_id)
            except OperationalError:
                # 消息中间件连不上：这条推迟 2、4、8……最多 60 秒再试，本轮剩下的也先不发了
                row.tries += 1
                delay = timedelta(seconds=min(2 ** row.tries, MAX_BACKOFF_SECONDS))
                row.next_attempt_at = func.now() + delay
                log.exception("投递 attempt %s 失败（第 %s 次）", row.attempt_id, row.tries)
                break
            row.status = "sent"
            row.sent_at = func.now()
            sent += 1
        return sent


def publish(attempt_id: int) -> None:
    # send_task 按任务名发消息，不在这里执行任务代码。测试里换成一个假的 publish
    celery_app.send_task("margin.execute_attempt", args=[attempt_id])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = get_engine()
    last_inspect = 0.0
    log.info("dispatcher 启动")
    while True:
        sent = dispatch_once(engine, publish)
        if sent:
            log.info("投递 %s 条", sent)
        if time.monotonic() - last_inspect >= INSPECT_EVERY_SECONDS:
            expired = lease.expire_leases(engine)
            if expired:
                log.warning("租约过期，判为失败：attempt %s", expired)
            last_inspect = time.monotonic()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
