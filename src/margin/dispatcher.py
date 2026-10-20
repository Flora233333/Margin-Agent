"""Dispatcher：把 outbox 里待投递的 attempt 发到 RabbitMQ 队列；顺带做租约巡检和消息丢失对账。

启动：python -m margin.dispatcher（compose 里的 dispatcher 服务）

为什么不在 API 里直接发 Celery 消息：见 runs._enqueue_attempt 的 Outbox 说明。
API 只负责把“要投递”写进 outbox 表（和建任务同一个事务）并 NOTIFY；这里平时在 LISTEN 上等通知，
被叫醒就扫一次 outbox，发出去后标记 sent。最多等 10 秒也会自己扫一次（兜底，见 main）。

投递语义是“至少一次”：消息发出去了、但标记 sent 的事务没提交成功（例如这时进程崩溃），
下一轮会再发一次。重复的消息没关系——worker 领取执行权时只有一个能成功（lease.claim）。

出错策略：发消息失败（RabbitMQ 连不上、拒收、迟迟不确认）是预期内的外部故障，记下失败次数、
推迟下次投递时间；数据库出错则让进程直接退出，由 compose 的 restart 策略重新拉起。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import psycopg
from amqp.exceptions import NotFound
from celery import Celery
from kombu import Connection
from kombu.exceptions import OperationalError
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from . import lease
from .db import get_engine
from .models import Attempt, Outbox, Run
from .runs import OUTBOX_CHANNEL, add_event, notify_outbox
from .settings import get_settings

log = logging.getLogger(__name__)

# 一轮的节奏：没有通知时最多等 10 秒也扫一次 outbox（兜底）；
# 巡检（过期租约 + 消息丢失对账）每 10 秒一次。
# 原来是两个数（兜底 10 秒、巡检 15 秒），合成一个好讲：出了事最多晚 10 秒被发现（M1.6）
TICK_SECONDS = 10.0
BATCH = 50  # 一轮最多投递多少条
MAX_BACKOFF_SECONDS = 60
CONFIRM_TIMEOUT_SECONDS = 5  # 发出一条消息后最多等 RabbitMQ 的“收到了”（发送确认）多久
SOCKET_TIMEOUT_SECONDS = 10  # 和 RabbitMQ 之间单次网络读 / 写最多等多久
# ---- 重发（resend_lost）----
RESEND_AFTER = timedelta(minutes=1)  # 发出 1 分钟还没 claim 就重发；之后每次间隔翻倍
MAX_REDELIVERIES = 3  # 最多重发 3 次，之后再等一个间隔还没 claim 就把 attempt 判为失败
BACKLOG_WARN_SECONDS = 120  # RabbitMQ 里就绪数连续 2 分钟不为 0：记 warning（M6 接告警）
TASK_QUEUE = "celery"  # Celery 默认的任务队列名；worker 和 dispatcher 都没改过它


def free_slots(session: Session, capacity: int) -> int:
    """worker 还有几个空位 = 总位子数 − 在途数（可能为负：worker 刚少了一个时）。

    capacity 是总位子数：消费者数 × 每个 worker 的子进程数（main 里按最近一次巡检算）。
    在途 = 已经交给 worker、还没结束的：attempt 是 running 的，加上已发出（outbox sent）
    但还没被 claim（attempt 仍是 pending）的。两个数在一条 SQL 里算，看到的是同一时刻的数据。
    running 那一半走部分索引 ix_attempts_running_lease（只收录 running 的行）。
    """
    running = (select(func.count()).select_from(Attempt)
               .where(Attempt.status == "running").scalar_subquery())
    unclaimed = (select(func.count()).select_from(Outbox)
                 .join(Attempt, Attempt.id == Outbox.attempt_id)
                 .where(Outbox.status == "sent", Attempt.status == "pending").scalar_subquery())
    return capacity - session.scalar(select(running + unclaimed))


def dispatch_once(engine: Engine, publish: Callable[[int], None], capacity: int) -> int:
    """投递一轮：worker 有几个空位就取几条到期的 pending 记录逐条发送，返回发送成功的条数。

    有空位才发（M1.6，D28）：消息只在 worker 能马上接手时才进 RabbitMQ，积压留在 outbox 里
    （pending，按 id 先进先出）。这样 RabbitMQ 里几乎没有排队的消息，“发出去很久还没被 claim”
    就只可能是丢了，对账只看时间就够（resend_lost）。capacity 为 0（还没读到队列状态、
    读不到、或者没有 worker 在线）时这一轮不发。

    FOR UPDATE SKIP LOCKED：锁住取出的行，别的 dispatcher 进程跳过它们去取别的行。
    现在只有一个 dispatcher，但以后多开几个也不会把同一条记录发两次。

    时间用 clock_timestamp() 而不是 now()：PG 的 now() 是“事务开始的时间”，整个事务里都不变。
    这一轮的事务从取记录开始，中间要等发送（失败时最多等 5 秒确认），用 now() 的话：
    退避“2 秒后再试”写进去时已经过期，等于没退避；sent_at 也会早于真正发出的时刻
    （重发按它算“发出多久了”）。clock_timestamp() 是语句真正执行时的时间。
    """
    with Session(engine) as session, session.begin():
        free = free_slots(session, capacity)
        if free <= 0:
            return 0
        rows = session.execute(
            select(Outbox)
            .where(Outbox.status == "pending", Outbox.next_attempt_at <= func.now())
            .order_by(Outbox.id).limit(min(free, BATCH))
            .with_for_update(skip_locked=True)
        ).scalars().all()
        sent = 0
        for row in rows:
            try:
                publish(row.attempt_id)
            except OperationalError:
                # 发送失败（连不上、拒收、等确认超时，实测都是这一个异常，见 make_publisher）：
                # 这条推迟 2、4、8……最多 60 秒再试，本轮剩下的也先不发了
                row.tries += 1
                delay = timedelta(seconds=min(2 ** row.tries, MAX_BACKOFF_SECONDS))
                row.next_attempt_at = func.clock_timestamp() + delay
                log.exception("投递 attempt %s 失败（第 %s 次）", row.attempt_id, row.tries)
                break
            row.status = "sent"
            row.sent_at = func.clock_timestamp()
            sent += 1
        return sent


def make_publisher(broker_url: str) -> Callable[[int], None]:
    """返回一个“把 attempt_id 发到队列、等到 RabbitMQ 确认收到才返回”的函数。

    只用来发消息的 Celery 实例：send_task 按任务名发送，不需要导入 worker 的任务代码。
    任务名、队列名（默认 celery）和 worker 那边一致，消息就会进同一个队列。

    发送确认（publisher confirm）：RabbitMQ 把消息写进队列（持久化消息要先写盘）后回一个“收到了”，
    这里收到才返回，dispatcher 才把 outbox 标记为 sent。没有确认时 sent 只代表“交给了网络”。
    三种失败实测都抛 kombu 的 OperationalError（2026-10-06，celery 5.6、RabbitMQ 4.3）：
        连不上              原因是 ConnectionRefusedError
        拒收（nack）        原因是 MessageNacked，例如队列满了且设置为拒收新消息
        迟迟不确认          原因是 TimeoutError，例如 RabbitMQ 内存 / 磁盘告警时会暂停所有发送方
    后两种必须设超时：等确认默认不设上限；而且实测只设 confirm_timeout 不够——超时后 kombu 关闭通道时
    还要等 RabbitMQ 回复，告警期间等不到，dispatcher 会永远卡在这里。socket 读写超时兜住这一步
    （容器里实测：告警时约 27 秒抛出 = 等确认 5 秒 + 关闭通道、连接各等一次读超时）。
    注意 read_timeout 只在 Linux 上按“秒”生效：py-amqp 按 Linux 的格式设置 SO_RCVTIMEO，
    Windows 会把它当成毫秒。dispatcher 只跑在容器里，所以没关系；在 Windows 上做实验时别被误导。

    task_publish_retry=False：Celery 默认会在发送失败时立即重试 3 次。dispatcher 自己有退避重试，
    再叠一层会让一次失败卡住 4 倍时间，所以关掉，失败直接交给 dispatch_once。
    """
    app = Celery("margin", broker=broker_url)
    app.conf.update(
        broker_transport_options={
            "confirm_publish": True,
            "read_timeout": SOCKET_TIMEOUT_SECONDS,
            "write_timeout": SOCKET_TIMEOUT_SECONDS,
        },
        task_publish_retry=False,
    )

    def publish(attempt_id: int) -> None:
        app.send_task("margin.execute_attempt", args=[attempt_id],
                      confirm_timeout=CONFIRM_TIMEOUT_SECONDS)

    return publish


@dataclass
class QueueState:
    """RabbitMQ 里任务队列此刻的状态。"""

    ready: int  # 就绪消息数：排队等着被取走的（不含已经推给 worker、还没确认的）
    consumers: int  # 消费者数：正在监听这个队列的 worker 通道


def read_queue_state(broker_url: str) -> QueueState | None:
    """用“被动声明”读任务队列的就绪消息数和消费者数；RabbitMQ 连不上时返回 None。

    被动声明（queue_declare passive=True）：只问“这个队列在不在、现在什么状态”，不创建、不修改队列。
    每次巡检（10 秒）开一条新连接、用完就关，比一直占着一条连接简单，开销可以忽略。
    max_retries=0：连不上就立刻返回，不在这里重试——下一轮巡检本来就会再试。
    """
    try:
        with Connection(broker_url, connect_timeout=5,
                        transport_options={"max_retries": 0}) as conn:
            _, ready, consumers = conn.default_channel.queue_declare(TASK_QUEUE, passive=True)
    except OperationalError:
        log.warning("读不到 RabbitMQ 队列状态：这一轮不投递、不判重发")
        return None
    except NotFound:
        # 队列不存在：worker 启动时、dispatcher 发消息时都会建它，不存在只能是 RabbitMQ 的数据没了
        # 而且 worker 也没在线。等同于“没有消费者”：不投递、不重发
        return QueueState(ready=0, consumers=0)
    return QueueState(ready, consumers)


def resend_lost(engine: Engine, queue: QueueState | None) -> tuple[list[int], list[int]]:
    """重发：消息发出去 1 分钟还没被 claim，就当它丢了，把 outbox 改回 pending 重新投递。

    queue 是 RabbitMQ 队列此刻的状态，读不到（RabbitMQ 连不上）时是 None。
    返回 (重发了的 attempt id, 重发次数用完、判为失败的 attempt id)。

    为什么需要：outbox 标成 sent 之后，消息还可能在 RabbitMQ 里丢掉（队列被清空、磁盘损坏），
    attempt 就会永远停在 pending——没有 worker 收到消息，重新生成也因为“还有执行没结束”被拒绝。
    PG 是唯一的裁判：判断错了的代价只是多一条消息，worker 领取时（lease.claim）会把重复的挡掉。

    为什么只看时间就够（M1.6，D28）：worker 收到消息先 claim（worker.execute），
    dispatcher 只在有空位时才发（dispatch_once），所以正常情况下“发出 → claim”只有几毫秒，
    RabbitMQ 里也没有排队的消息。
    发出 1 分钟还没 claim，只可能是丢了。

    判定：attempt 是 pending、outbox 是 sent、sent_at 早于 now − 1 分钟 × 2^重发次数。
    即第一次发出 1 分钟后重发，之后间隔 2、4 分钟（每次重发由投递更新 sent_at）；
    第 3 次重发后再过 8 分钟还没 claim，判 delivery_lost（从第一次发出算约 15 分钟）。
    一直送不到说明问题不是偶发丢消息（例如 worker 不认识这个任务名），继续发只会掩盖问题。

    前提（不满足就这一轮不判，10 秒后再看）：
        读得到队列状态      读不到时不知道 worker 在不在；
        消费者数不为 0      没有 worker 在线，发了也没人收——这是“worker 全挂了”，交给告警；
        就绪数为 0          有了“有空位才发”，正常时几乎总是 0。不为 0 说明出了别的问题
                            （子进程数配错、RabbitMQ 刚重启、worker 卡住），消息其实还在队列里排着，
                            这时按时间判会把排队的当成丢了。
    """
    if queue is None or queue.consumers == 0 or queue.ready > 0:
        return [], []
    with Session(engine) as session, session.begin():
        # 候选：发出去了还没被 claim 的（正常时只有刚发出的几条）。到没到时间在下面逐条算，好读
        candidates = session.execute(
            select(Outbox.id, Outbox.attempt_id, Outbox.sent_at, Outbox.redeliveries)
            .join(Attempt, Attempt.id == Outbox.attempt_id)
            .where(Outbox.status == "sent", Attempt.status == "pending")
        ).all()
        now = session.scalar(select(func.now()))
        resent, failed = [], []
        for outbox_id, attempt_id, sent_at, redeliveries in candidates:
            if sent_at > now - RESEND_AFTER * 2 ** redeliveries:
                continue  # 还没到时间（刚发出，或者上次重发后还在等）
            if redeliveries >= MAX_REDELIVERIES:
                if _fail_undelivered(session, attempt_id):
                    failed.append(attempt_id)
                continue
            # 复用同一行 outbox：改回 pending，由 dispatch_once 去发（发送失败照样按 tries 退避，
            # 不算进重发次数；RabbitMQ 告警时一条要约 27 秒才报错，也不会卡住巡检）。
            # 不新插一行：“同一个 attempt 最多一条 pending”的部分唯一索引仍然成立。
            # 带 status='sent' 条件：和 dispatcher 自己的投递不会互相覆盖。
            # 不用 NOTIFY：改回的行立刻到期，main 接下来就会投递（seconds_until_due 返回 0）
            session.execute(
                update(Outbox).where(Outbox.id == outbox_id, Outbox.status == "sent")
                .values(status="pending", next_attempt_at=func.now(),
                        redeliveries=Outbox.redeliveries + 1)
            )
            resent.append(attempt_id)
        return resent, failed


def _fail_undelivered(session: Session, attempt_id: int) -> bool:
    """补发次数用完：attempt 判为失败（delivery_lost），run 跟着失败，写 attempt_failed 事件。

    WHERE status='pending'：巡检判定的同时 worker 可能刚好领取了它（已是 running），这时更新 0 行、
    什么都不做，返回 False。epoch 加 1：和巡检判租约过期一样，状态每次被“收回”都让执行权换一代。
    """
    run_id = session.execute(
        update(Attempt).where(Attempt.id == attempt_id, Attempt.status == "pending")
        .values(status="failed", error="delivery_lost", lease_epoch=Attempt.lease_epoch + 1,
                finished_at=func.now())
        .returning(Attempt.run_id)
    ).scalar_one_or_none()
    if run_id is None:
        return False
    session.execute(update(Run).where(Run.id == run_id).values(status="failed"))
    add_event(session, run_id, "attempt_failed",
              {"attempt_id": attempt_id, "error": "delivery_lost"})
    # 它原来算在“在途”里（已发出、没 claim），判失败后空出一个位子：叫醒投递循环去补位
    notify_outbox(session)
    return True


def listen(database_url: str) -> psycopg.Connection:
    """开一条专用连接，在上面 LISTEN outbox 频道。

    为什么专用、不从连接池借：LISTEN 是“这条连接”在听，通知只会送到这条连接上；
    池里的连接用完就还回去给别人用了。另外 PG 只在连接不处于未提交的事务里时才把通知交出来，
    所以用自动提交（autocommit）模式：每条语句执行完就提交，连接永远不会卡在一个打开的事务里。
    """
    conn = psycopg.connect(database_url, autocommit=True)
    conn.execute(f"LISTEN {OUTBOX_CHANNEL}")
    return conn


def wait_for_notify(listener: psycopg.Connection, timeout: float) -> bool:
    """最多等 timeout 秒，收到 outbox 通知就立刻返回 True，超时返回 False。

    notifies() 在等待期间不占 CPU（阻塞在 socket 上），和 time.sleep 一样省，但有通知时马上醒。
    连接断了（PG 重启）会抛 psycopg.OperationalError：不捕获，让进程退出、由 compose 重启，
    重启后重新 LISTEN；中间错过的通知由兜底扫描补上（通知不持久，没人在听就丢了）。
    """
    for _ in listener.notifies(timeout=timeout, stop_after=1):
        return True
    return False


def seconds_until_due(engine: Engine, capacity: int) -> float:
    """离下一条 pending 记录可以投递还有几秒（最多 TICK_SECONDS）。

    通知只负责“有新任务”“有题结束、空出位子”。投递失败后退避的记录到期时不会有人发通知，
    所以等待时间不能超过它的到期时间；已经到期的（例如一轮超过 BATCH 条没发完）返回 0，
    马上再扫一轮。
    没有空位时到期了也发不出去，返回 TICK_SECONDS：等有题结束的通知，或者下一次巡检
    （消费者数可能变了）。不这样判断的话，积压时这里一直返回 0，循环会空转、不停地查库。
    """
    with Session(engine) as session:
        if free_slots(session, capacity) <= 0:
            return TICK_SECONDS
        due = session.scalar(
            select(func.extract("epoch", func.min(Outbox.next_attempt_at) - func.now()))
            .where(Outbox.status == "pending")
        )
    if due is None:
        return TICK_SECONDS
    return max(0.0, min(float(due), TICK_SECONDS))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    engine = get_engine()
    publish = make_publisher(settings.broker_url)
    # 先 LISTEN 再进循环：启动前就已经在 outbox 里的记录由第一轮扫描处理，之后的靠通知
    listener = listen(settings.database_url)
    last_inspect = 0.0
    queue: QueueState | None = None  # 最近一次巡检读到的队列状态；读不到时是 None
    backlog_since: float | None = None  # RabbitMQ 里的就绪数从什么时候起一直不为 0
    backlog_warned = False  # 这一段积压记过 warning 了没有
    log.info("dispatcher 启动")
    while True:
        # 巡检按自己的计时器：通知随时会叫醒循环（每提交一道题就醒一次），不能醒一次就巡检一次。
        # 放在投递前面：启动后的第一轮要先读到队列状态（有几个 worker），才知道能发几条
        if time.monotonic() - last_inspect >= TICK_SECONDS:
            queue = read_queue_state(settings.broker_url)
            expired = lease.expire_leases(engine)
            if expired:
                log.warning("租约过期，判为失败：attempt %s", expired)
            resent, failed = resend_lost(engine, queue)
            if resent:
                # M6 接监控：重发次数 > 0 就告警，说明消息中转站出了问题
                log.warning("发出 1 分钟以上仍没有 worker 领取，重发：attempt %s", resent)
            if failed:
                log.error("重发 %s 次仍未送达，判为失败：attempt %s", MAX_REDELIVERIES, failed)
            # 有空位才发以后，就绪数正常时几乎总是 0；一直不为 0 时重发也暂停了，要让人知道。
            # 每段积压只在满 2 分钟时记一条，不每 10 秒刷一次
            if queue is not None and queue.ready > 0:
                if backlog_since is None:
                    backlog_since = time.monotonic()
                if not backlog_warned and time.monotonic() - backlog_since >= BACKLOG_WARN_SECONDS:
                    log.warning("RabbitMQ 里有 %s 条消息排队超过 2 分钟（子进程数配错？"
                                "worker 卡住？），这期间不判重发", queue.ready)
                    backlog_warned = True
            else:
                backlog_since, backlog_warned = None, False
            last_inspect = time.monotonic()
        # 总位子数用最近一次巡检的消费者数（最多 10 秒前），不在每次投递时都连一次 RabbitMQ
        capacity = queue.consumers * settings.worker_concurrency if queue else 0
        sent = dispatch_once(engine, publish, capacity)
        if sent:
            log.info("投递 %s 条", sent)
        # 等到：有通知、或最早的退避记录到期、或该巡检了（最多 10 秒）——哪个先到算哪个
        until_inspect = TICK_SECONDS - (time.monotonic() - last_inspect)
        wait_for_notify(listener, max(0.0, min(seconds_until_due(engine, capacity), until_inspect)))


if __name__ == "__main__":
    main()
