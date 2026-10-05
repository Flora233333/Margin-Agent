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

MAX_WAIT_SECONDS = 10.0  # 没有通知时最多等这么久也扫一次 outbox（兜底）
INSPECT_EVERY_SECONDS = 15  # 每 15 秒巡检一次：过期租约 + 消息丢失对账
BATCH = 50  # 一轮最多投递多少条
MAX_BACKOFF_SECONDS = 60
CONFIRM_TIMEOUT_SECONDS = 5  # 发出一条消息后最多等 RabbitMQ 的“收到了”（发送确认）多久
SOCKET_TIMEOUT_SECONDS = 10  # 和 RabbitMQ 之间单次网络读 / 写最多等多久
# ---- 对账（requeue_lost）----
GRACE = timedelta(minutes=2)  # 投递后至少等这么久才可能判“丢了”
WATERMARK_WINDOW = timedelta(hours=1)  # 水位线只看最近 1 小时投递的记录，不扫全表
FALLBACK = timedelta(minutes=30)  # 前两条规则都判断不了时，投递超过 30 分钟就算丢了
MAX_REDELIVERIES = 3  # 最多补发 3 次，之后把 attempt 判为失败
TASK_QUEUE = "celery"  # Celery 默认的任务队列名；worker 和 dispatcher 都没改过它


def dispatch_once(engine: Engine, publish: Callable[[int], None]) -> int:
    """投递一轮：取出到期的 pending 记录逐条发送，返回发送成功的条数。

    FOR UPDATE SKIP LOCKED：锁住取出的行，别的 dispatcher 进程跳过它们去取别的行。
    现在只有一个 dispatcher，但以后多开几个也不会把同一条记录发两次。

    时间用 clock_timestamp() 而不是 now()：PG 的 now() 是“事务开始的时间”，整个事务里都不变。
    这一轮的事务从取记录开始，中间要等发送（失败时最多等 5 秒确认），用 now() 的话：
    退避“2 秒后再试”写进去时已经过期，等于没退避；同一批发出的记录 sent_at 也都一样，
    对账的水位线就分不出谁先谁后。clock_timestamp() 是语句真正执行时的时间。
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
    每次巡检（15 秒）开一条新连接、用完就关，比一直占着一条连接简单，开销可以忽略。
    max_retries=0：连不上就立刻返回，不在这里重试——下一轮巡检本来就会再试。
    """
    try:
        with Connection(broker_url, connect_timeout=5,
                        transport_options={"max_retries": 0}) as conn:
            _, ready, consumers = conn.default_channel.queue_declare(TASK_QUEUE, passive=True)
    except OperationalError:
        log.warning("读不到 RabbitMQ 队列状态，本轮对账跳过“队列为空”规则")
        return None
    except NotFound:
        # 队列不存在：worker 启动时、dispatcher 发消息时都会建它，不存在只能是 RabbitMQ 的数据没了
        # 而且 worker 也没在线。等同于“没有消费者”，本轮不补发
        return QueueState(ready=0, consumers=0)
    return QueueState(ready, consumers)


def requeue_lost(engine: Engine, queue: QueueState | None) -> tuple[list[int], list[int]]:
    """对账：找出“消息已投递、却一直没人领取”的 attempt，把它的 outbox 改回 pending 重新投递。

    queue 是 RabbitMQ 队列此刻的状态；读不到（RabbitMQ 连不上）时传 None，跳过“队列为空”这条规则。
    返回 (补发了的 attempt id, 补发次数用完、判为失败的 attempt id)。

    为什么需要：outbox 标成 sent 之后，消息还可能在 RabbitMQ 里丢掉（队列被清空、磁盘损坏），
    attempt 就会永远停在 pending——没有 worker 收到消息，重新生成也因为“还有执行没结束”被拒绝。
    PG 是唯一的裁判：判断错了的代价只是多一条消息，worker 领取时（lease.claim）会把重复的挡掉。

    候选：attempt 是 pending、outbox 是 sent、投递已超过宽限期 2 分钟。宽限期吸收两件事：
    几个 worker 子进程同时取消息时领取顺序的毫秒级抖动；子进程第一次执行任务前要加载语料（约 2 秒，
    缓存丢失时更久）。候选满足下面任一条就判定丢了：
        ① 水位线：有比我晚投递的消息已经被领取了。队列先进先出，后面的都被取走了我还在，说明我丢了；
        ② 队列为空：RabbitMQ 里没有就绪消息，我却还没被领取（正在执行的消息是“已推送未确认”，
           不算就绪；它们对应的 attempt 已经是 running，不会是候选）；
        ③ 兜底：投递超过 30 分钟（前两条都判断不了时，例如读不到队列状态、又没有新流量）。
    消费者数为 0 时什么都不做：没有 worker 在线，补发了也没人收——这是“worker 全挂了”，交给告警。

    补发上限 3 次：第 4 次判定丢失时不再补发，把 attempt 判为失败（delivery_lost），
    用户可以点“重新生成”。一直补不进去，说明问题不是偶发丢消息，继续补只会掩盖问题。
    """
    if queue is not None and queue.consumers == 0:
        return [], []
    with Session(engine) as session, session.begin():
        candidates = session.execute(
            select(Outbox.id, Outbox.attempt_id, Outbox.sent_at, Outbox.redeliveries)
            .join(Attempt, Attempt.id == Outbox.attempt_id)
            .where(Outbox.status == "sent", Attempt.status == "pending",
                   Outbox.sent_at < func.now() - GRACE)
        ).all()
        if not candidates:
            return [], []
        # 水位线 = 已被领取的消息里最晚的投递时间。“已被领取”看 started_at（lease.claim 时写入），
        # 不看 status：判为 delivery_lost 的 attempt 状态也不是 pending，但它从来没被领取过
        watermark = session.scalar(
            select(func.max(Outbox.sent_at))
            .join(Attempt, Attempt.id == Outbox.attempt_id)
            .where(Outbox.status == "sent", Attempt.started_at.is_not(None),
                   Outbox.sent_at > func.now() - WATERMARK_WINDOW)
        )
        now = session.scalar(select(func.now()))
        requeued, failed = [], []
        for outbox_id, attempt_id, sent_at, redeliveries in candidates:
            lost = ((watermark is not None and sent_at < watermark)
                    or (queue is not None and queue.ready == 0)
                    or sent_at < now - FALLBACK)
            if not lost:
                continue
            if redeliveries >= MAX_REDELIVERIES:
                if _fail_undelivered(session, attempt_id):
                    failed.append(attempt_id)
                continue
            # 复用同一行 outbox：改回 pending，下一轮 dispatch_once 就会投递
            # （sent_at 随之更新，重新排到队尾）。不新插一行：outbox 上
            # “同一个 attempt 最多一条 pending”的部分唯一索引仍然成立。
            # 带 status='sent' 条件：和 dispatcher 自己的投递不会互相覆盖
            session.execute(
                update(Outbox).where(Outbox.id == outbox_id, Outbox.status == "sent")
                .values(status="pending", next_attempt_at=func.now(),
                        redeliveries=Outbox.redeliveries + 1)
            )
            requeued.append(attempt_id)
        if requeued:
            notify_outbox(session)  # 改回 pending 也是“有新的待投递”，叫醒投递循环
        return requeued, failed


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


def seconds_until_due(engine: Engine) -> float:
    """离最早一条 pending 记录可以投递还有几秒（最多 MAX_WAIT_SECONDS）。

    通知只负责“有新任务”。投递失败后退避的记录到期时不会有人发通知，所以等待时间不能超过它的到期时间；
    已经到期的（例如一轮超过 BATCH 条没发完）返回 0，马上再扫一轮。
    """
    with Session(engine) as session:
        due = session.scalar(
            select(func.extract("epoch", func.min(Outbox.next_attempt_at) - func.now()))
            .where(Outbox.status == "pending")
        )
    if due is None:
        return MAX_WAIT_SECONDS
    return max(0.0, min(float(due), MAX_WAIT_SECONDS))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    engine = get_engine()
    publish = make_publisher(settings.broker_url)
    # 先 LISTEN 再进循环：启动前就已经在 outbox 里的记录由第一轮扫描处理，之后的靠通知
    listener = listen(settings.database_url)
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
            requeued, failed = requeue_lost(engine, read_queue_state(settings.broker_url))
            if requeued:
                # M6 接监控：补发次数 > 0 就告警，说明消息中转站出了问题
                log.warning("消息疑似丢失，补发：attempt %s", requeued)
            if failed:
                log.error("补发 %s 次仍未送达，判为失败：attempt %s", MAX_REDELIVERIES, failed)
            last_inspect = time.monotonic()
        # 等到：有通知、或最早的退避记录到期、或该巡检了、或 10 秒——哪个先到算哪个
        until_inspect = INSPECT_EVERY_SECONDS - (time.monotonic() - last_inspect)
        wait_for_notify(listener, max(0.0, min(seconds_until_due(engine), until_inspect)))


if __name__ == "__main__":
    main()
