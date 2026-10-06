"""租约（lease）+ epoch：决定“现在谁有权执行这个 attempt”，并挡住被取代的旧 worker。

为什么需要：同一个 attempt 可能被投递不止一次（outbox 重复投递、消息重发），
也可能执行它的 worker 卡住、失联。数据库是唯一的裁判：
    领取   一条条件 UPDATE：只有 pending 的 attempt 能被领取，领到的人拿到新的 epoch；
    续租   执行期间每 15 秒把 lease_until 往后推 90 秒；
    提交   每一步写库时检查 epoch 仍是自己的（fencing token），不是就整个事务回滚、停止执行；
    巡检   租约过期（worker 死了或卡住）的 attempt 标成失败并把 epoch 加 1，
           旧 worker 就算之后醒来，写库也会被拒绝。
判断“过期”只是 lease_until < now() 的比较，时间一律用数据库的 now()，不用各台机器自己的钟。

M1 的范围：领取只接受 pending；租约过期直接判失败（用户可点“重新生成”）。
M4 再加“接管”：过期的 attempt 由别的 worker 领取、从断点继续。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .harness import Step
from .models import Attempt, Run
from .models import Step as StepRow
from .runs import add_event

log = logging.getLogger(__name__)

LEASE = timedelta(seconds=90)  # 租约长度：超过 90 秒没续租就算失联
RENEW_EVERY_SECONDS = 15  # 续租间隔：90 秒内有 6 次机会，偶尔一两次失败不会丢租约


class LeaseLost(Exception):
    """执行权已经不是自己的了（被巡检判定过期）。收到它就停止，不再写任何东西。"""


@dataclass
class Lease:
    """领取成功后拿到的执行权，以及执行需要的题目信息。"""

    attempt_id: int
    run_id: int
    epoch: int
    model: str
    task: dict[str, Any]  # 交给 Harness 的题目：question / options / answer_format


def claim(engine: Engine, attempt_id: int, worker: str) -> Lease | None:
    """领取执行权。返回 None 表示不该由我执行（重复投递，或者已经结束）。

    “检查状态”和“改成我的”在同一条 UPDATE 里完成：PG 执行 UPDATE 时会锁住这一行再检查 WHERE，
    两个 worker 同时领取，只有一个能把 pending 改成 running，另一个更新 0 行。
    如果写成“先 SELECT 看看是不是 pending，再 UPDATE”，两个 worker 可能同时看到 pending。
    """
    with Session(engine) as session, session.begin():
        row = session.execute(
            update(Attempt).where(Attempt.id == attempt_id, Attempt.status == "pending")
            .values(status="running", lease_owner=worker, lease_epoch=Attempt.lease_epoch + 1,
                    lease_until=func.now() + LEASE, started_at=func.now())
            .returning(Attempt.run_id, Attempt.lease_epoch, Attempt.model)
        ).one_or_none()
        if row is None:
            return None
        session.execute(update(Run).where(Run.id == row.run_id).values(status="running"))
        # epoch 也告诉前端：实时片段带着 (attempt_id, epoch)，前端只显示和当前执行一致的片段
        add_event(session, row.run_id, "attempt_started",
                  {"attempt_id": attempt_id, "epoch": row.lease_epoch})
        run = session.execute(select(Run).where(Run.id == row.run_id)).scalar_one()
        task = {"question": run.question, "options": run.options,
                "answer_format": run.answer_format}
        return Lease(attempt_id, row.run_id, row.lease_epoch, row.model, task)


def _fence(session: Session, lease: Lease, **values: Any) -> None:
    """带 epoch 条件更新 attempt；更新到 0 行说明执行权已被取代，抛出 LeaseLost。

    抛出时外层的 session.begin() 会回滚整个事务：同一事务里准备写的步骤、事件全部作废。
    """
    result = session.execute(
        update(Attempt)
        .where(Attempt.id == lease.attempt_id, Attempt.lease_epoch == lease.epoch,
               Attempt.status == "running")
        .values(**values)
    )
    if result.rowcount == 0:
        raise LeaseLost


def commit_step(engine: Engine, lease: Lease, step: Step, citation_no: int | None = None) -> None:
    """把一步写进数据库：校验 epoch + 续租 + 写步骤 + 写事件，在同一个事务里。

    citation_no：这一步是通过校验的引用时的编号（citations.py），随 step 事件下发给前端；
    其他步骤为 None。
    """
    with Session(engine) as session, session.begin():
        _fence(session, lease, last_step=step.turn, lease_until=func.now() + LEASE)
        session.add(StepRow(
            attempt_id=lease.attempt_id, step_no=step.turn, tool_name=step.tool_name,
            arguments=step.arguments, result=step.result, reasoning=step.reasoning,
            llm_ms=round(step.llm_seconds * 1000),
            prompt_tokens=step.usage.get("prompt_tokens", 0),
            completion_tokens=step.usage.get("completion_tokens", 0),
        ))
        add_event(session, lease.run_id, "step", {
            "attempt_id": lease.attempt_id, "step_no": step.turn, "tool_name": step.tool_name,
            "arguments": step.arguments, "result": step.result, "reasoning": step.reasoning,
            "citation_no": citation_no,
        })


def finish(engine: Engine, lease: Lease, final: dict[str, Any] | None,
           violation: str | None) -> None:
    """Harness 正常结束（提交了答案，或触发了停止条件）。"""
    with Session(engine) as session, session.begin():
        _fence(session, lease, status="completed", final=final, violation=violation,
               finished_at=func.now())
        session.execute(update(Run).where(Run.id == lease.run_id).values(status="completed"))
        add_event(session, lease.run_id, "attempt_finished",
                  {"attempt_id": lease.attempt_id, "final": final, "violation": violation})


def fail(engine: Engine, lease: Lease, error: str) -> None:
    """执行出错（网关报错、超时……）。"""
    with Session(engine) as session, session.begin():
        _fence(session, lease, status="failed", error=error, finished_at=func.now())
        session.execute(update(Run).where(Run.id == lease.run_id).values(status="failed"))
        add_event(session, lease.run_id, "attempt_failed",
                  {"attempt_id": lease.attempt_id, "error": error})


def renew(engine: Engine, lease: Lease) -> bool:
    """续租。返回 False 表示执行权已经不是自己的了。"""
    with Session(engine) as session, session.begin():
        result = session.execute(
            update(Attempt)
            .where(Attempt.id == lease.attempt_id, Attempt.lease_epoch == lease.epoch,
                   Attempt.status == "running")
            .values(lease_until=func.now() + LEASE)
        )
        return result.rowcount == 1


def expire_leases(engine: Engine) -> list[int]:
    """巡检：把租约已过期的 running attempt 判为失败，返回这些 attempt 的 id。

    epoch 同时加 1：原来的 worker 如果只是卡住、之后又醒来，它手里的 epoch 已经过时，
    提交步骤、续租、写结果都会被拒绝。
    """
    with Session(engine) as session, session.begin():
        rows = session.execute(
            update(Attempt).where(Attempt.status == "running", Attempt.lease_until < func.now())
            .values(status="failed", error="lease_expired", lease_epoch=Attempt.lease_epoch + 1,
                    finished_at=func.now())
            .returning(Attempt.id, Attempt.run_id)
        ).all()
        for attempt_id, run_id in rows:
            session.execute(update(Run).where(Run.id == run_id).values(status="failed"))
            add_event(session, run_id, "attempt_failed",
                      {"attempt_id": attempt_id, "error": "lease_expired"})
        return [attempt_id for attempt_id, _ in rows]


class LeaseKeeper:
    """续租线程：执行期间每 15 秒续一次租。用法：with LeaseKeeper(engine, lease): 执行……

    为什么要单独的线程：主线程一次模型调用可能要两三分钟（实测单轮最长 120 秒），
    只靠“每提交一步顺带续租”，慢的那一轮里 90 秒的租约就过期了。
    M4 在这里加上超时检查和卡死判定（监督者）。
    """

    def __init__(self, engine: Engine, lease: Lease) -> None:
        self.engine = engine
        self.lease = lease
        self._stop = threading.Event()
        # daemon：主线程退出时不用等它
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        # Event.wait(15) 等价于“睡 15 秒”，但 stop 被设置时会立刻醒来
        while not self._stop.wait(RENEW_EVERY_SECONDS):
            try:
                if not renew(self.engine, self.lease):
                    # 已被取代：不用通知主线程，它下一次提交步骤时会被拒绝并停止
                    log.warning("attempt %s 的租约已失效，停止续租", self.lease.attempt_id)
                    return
            except OperationalError:
                # 数据库暂时连不上（例如重启）：下一轮再试，90 秒的租约够等几次
                log.exception("attempt %s 续租失败，稍后重试", self.lease.attempt_id)

    def __enter__(self) -> LeaseKeeper:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join()
