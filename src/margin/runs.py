"""任务的创建与查询（API 这一侧的数据库操作）。

每个函数是一个完整的事务：`with Session(engine) as session, session.begin():`
块正常结束就 COMMIT，中途抛异常就 ROLLBACK，不会留下“写了一半”的数据。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Attempt, Event, Outbox, Run, Step

OUTBOX_CHANNEL = "outbox"  # LISTEN / NOTIFY 的频道名：有新的 outbox 待投递
RUN_EVENTS_CHANNEL = "run_events"  # 频道名：某个 run 有新事件（附带 run_id），见 event_hub.py


class RunNotFound(Exception):
    """run 不存在，或者不属于这个用户（两种情况对外一样处理，不暴露别人的 run 是否存在）。"""


class IdempotencyConflict(Exception):
    """同一个幂等键，这次提交的内容和第一次不同。"""


class RunBusy(Exception):
    """这道题还有执行没结束，不能重新生成。"""


def add_event(session: Session, run_id: int, type_: str, payload: dict[str, Any]) -> int:
    """给 run 追加一个事件，返回它的序号 seq。

    序号 = runs.last_seq 加 1。UPDATE 会给这一行 run 加锁直到事务提交，所以同一个 run 的两个事务
    不可能拿到同一个序号，而且后拿序号的一定后提交。SSE 按“seq 大于上次收到的”去取，就不会漏掉事件。
    （如果直接用全表自增 id 当序号：事务 A 拿到 10、B 拿到 11，B 先提交，读者先看到 11，
    之后 A 提交的 10 就被跳过了。）
    """
    seq = session.execute(
        update(Run).where(Run.id == run_id).values(last_seq=Run.last_seq + 1)
        .returning(Run.last_seq)
    ).scalar_one()
    session.add(Event(run_id=run_id, seq=seq, type=type_, payload=payload))
    # 通知 API 进程“这个 run 有新事件了”（D20）。和 outbox 的通知一样跟着事务走：提交后才发出，
    # 所以 SSE 被叫醒去查库时一定查得到这条事件。payload 只带 run_id，事件内容仍然从库里按 seq 读。
    # 同一事务里同一个 run 的多次通知会被 PG 合并成一条
    session.execute(text("SELECT pg_notify(:channel, :run_id)"),
                    {"channel": RUN_EVENTS_CHANNEL, "run_id": str(run_id)})
    return seq


def notify_outbox(session: Session) -> None:
    """通知 dispatcher：“outbox 里有新的待投递记录了”（PG 的 LISTEN / NOTIFY）。

    NOTIFY 跟着事务走：事务提交后才真正发出，回滚则取消。所以 dispatcher 被叫醒时，
    这一行 outbox 一定已经能查到。同一事务里发多次相同的通知，PG 只会送出一条。
    dispatcher 那边一条专用连接执行了 LISTEN outbox，见 dispatcher.listen。
    """
    session.execute(text(f"NOTIFY {OUTBOX_CHANNEL}"))


def _enqueue_attempt(session: Session, run_id: int, attempt_no: int, trigger: str,
                     model: str) -> None:
    """新建一次执行，并在同一个事务里写一条 outbox 记录（“要把它投递给 worker”）。

    这就是 Outbox 模式：不在这里直接发 Celery 消息。如果“写库”和“发消息”分两步，
    中间进程崩溃就会出现“任务记下了却永远没人执行”；写进同一个事务，要么都有，要么都没有。
    真正的投递由 dispatcher 读 outbox 表完成。
    """
    attempt = Attempt(run_id=run_id, attempt_no=attempt_no, trigger=trigger, model=model)
    session.add(attempt)
    session.flush()  # 先把 INSERT 发给数据库，拿到自增的 attempt.id（事务还没提交）
    session.add(Outbox(attempt_id=attempt.id))
    notify_outbox(session)
    session.execute(update(Run).where(Run.id == run_id).values(status="queued"))
    add_event(session, run_id, "attempt_queued",
              {"attempt_id": attempt.id, "attempt_no": attempt_no, "trigger": trigger})


def create_run(engine: Engine, owner_id: int, idempotency_key: str, question: str,
               options: dict[str, str] | None, answer_format: str,
               model: str) -> tuple[int, bool]:
    """提交一道题，返回 (run_id, 是否新建)。

    幂等：前端每次提交带一个幂等键；网络超时后重试、用户连点两下，用的是同一个键，
    只会建一个 run。靠的是 runs(owner_id, idempotency_key) 唯一约束 + ON CONFLICT DO NOTHING：
    两个请求同时到达时，后一个的 INSERT 会等前一个事务结束，然后发现冲突、什么都不插。
    """
    with Session(engine) as session, session.begin():
        run_id = session.execute(
            insert(Run).values(owner_id=owner_id, idempotency_key=idempotency_key,
                               question=question, options=options,
                               answer_format=answer_format, model=model)
            .on_conflict_do_nothing(index_elements=["owner_id", "idempotency_key"])
            .returning(Run.id)
        ).scalar_one_or_none()
        if run_id is None:  # 这个键之前提交过
            existing = session.execute(
                select(Run).where(Run.owner_id == owner_id,
                                  Run.idempotency_key == idempotency_key)
            ).scalar_one()
            if (existing.question, existing.options, existing.answer_format) != (
                    question, options, answer_format):
                raise IdempotencyConflict
            return existing.id, False
        _enqueue_attempt(session, run_id, attempt_no=1, trigger="submit", model=model)
        return run_id, True


def regenerate(engine: Engine, owner_id: int, run_id: int) -> int:
    """用户点“重新生成”：新建一次执行，从头再跑。旧的执行和步骤都保留。返回新的 attempt_no。"""
    with Session(engine) as session, session.begin():
        # FOR UPDATE 锁住这一行 run：两个“重新生成”请求同时到达时排队执行，
        # 后一个能看到前一个刚建的 pending 执行，从而被拒绝，而不是建出两个
        run = session.execute(
            select(Run).where(Run.id == run_id, Run.owner_id == owner_id).with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise RunNotFound
        active = session.scalar(
            select(func.count()).select_from(Attempt)
            .where(Attempt.run_id == run_id, Attempt.status.in_(["pending", "running"]))
        )
        if active:
            raise RunBusy
        attempt_no = session.scalar(
            select(func.max(Attempt.attempt_no)).where(Attempt.run_id == run_id)) + 1
        _enqueue_attempt(session, run_id, attempt_no, trigger="regenerate", model=run.model)
        return attempt_no


def get_run(engine: Engine, owner_id: int, run_id: int) -> dict[str, Any]:
    """一道题的完整记录：题目、状态、每次执行及其每一步。"""
    with Session(engine) as session:
        run = session.execute(
            select(Run).where(Run.id == run_id, Run.owner_id == owner_id)
        ).scalar_one_or_none()
        if run is None:
            raise RunNotFound
        attempts = session.execute(
            select(Attempt).where(Attempt.run_id == run_id).order_by(Attempt.attempt_no)
        ).scalars().all()
        steps = session.execute(
            select(Step).where(Step.attempt_id.in_([a.id for a in attempts]))
            .order_by(Step.attempt_id, Step.step_no)
        ).scalars().all()
        return {
            "id": run.id, "question": run.question, "options": run.options,
            "answer_format": run.answer_format, "model": run.model, "status": run.status,
            "created_at": run.created_at,
            "attempts": [
                {"attempt_no": a.attempt_no, "trigger": a.trigger, "status": a.status,
                 "final": a.final, "violation": a.violation, "error": a.error,
                 "started_at": a.started_at, "finished_at": a.finished_at,
                 "steps": [
                     {"step_no": s.step_no, "tool_name": s.tool_name, "arguments": s.arguments,
                      "result": s.result, "reasoning": s.reasoning, "llm_ms": s.llm_ms}
                     for s in steps if s.attempt_id == a.id
                 ]}
                for a in attempts
            ],
        }


def events_after(engine: Engine, owner_id: int, run_id: int,
                 after_seq: int) -> tuple[list[Event], str]:
    """取 seq 大于 after_seq 的事件（按 seq 排序），以及 run 当前的状态。

    先读状态、后读事件，顺序不能反：PG 默认的隔离级别下，每条语句看到的是它开始时已提交的数据。
    如果先读事件、后读状态，两条语句之间恰好有“执行结束”的事务提交，就会读到“已结束”
    却漏掉“结束”事件，SSE 随即关闭，前端永远收不到结果。先读状态则没有这个问题：
    状态已是结束，说明同一事务写的结束事件也已提交，后面读事件一定能读到。
    """
    with Session(engine) as session:
        status = session.scalar(
            select(Run.status).where(Run.id == run_id, Run.owner_id == owner_id))
        if status is None:
            raise RunNotFound
        events = session.execute(
            select(Event).where(Event.run_id == run_id, Event.seq > after_seq)
            .order_by(Event.seq)
        ).scalars().all()
        return list(events), status
