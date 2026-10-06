"""后台 worker：从 RabbitMQ 队列收到 attempt_id，领取执行权，运行 Harness，每一步写进数据库。

启动（Linux / 容器里；Celery 的多进程模式不支持 Windows）：
    celery -A margin.worker worker --concurrency 4 --loglevel INFO
--concurrency 4 = 4 个子进程，每个同一时间只执行一个任务。

Celery 是什么：Python 的后台任务框架。dispatcher 往 RabbitMQ 的队列里放一条消息
“执行 attempt 12”，worker 进程从队列取消息、调用下面的 execute_attempt(12)。
队列只负责“叫醒 worker”；这个 attempt 该不该由我执行，由数据库里的租约决定（lease.py）。
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from functools import cache

import httpx
import redis
from celery import Celery
from sqlalchemy import Engine

from . import lease
from .assembly import build_llm, build_retriever
from .citations import CitationNumbers
from .db import get_engine
from .harness import build_registry, run_episode
from .live import DeltaPublisher, make_redis
from .llm import ChatModel
from .retrieval import Corpus, HybridRetriever
from .settings import get_settings

log = logging.getLogger(__name__)

celery_app = Celery("margin", broker=get_settings().broker_url)
celery_app.conf.update(
    # 任务执行完才确认（ack）消息。默认是“取到就确认”：worker 进程取到后崩溃，消息就丢了。
    # RabbitMQ 的确认是原生的：worker 的连接一断（进程被杀、容器重启），它没确认的消息立刻回到队列，
    # 不用像 Redis 那样等“可见性超时”。确认期限 consumer_timeout 见 deploy/rabbitmq/margin.conf
    task_acks_late=True,
    # 每个子进程只预取 1 条：手上只有正在执行的那一条，不会把别的任务压在自己这里排队。
    # 在管理界面里看：4 个子进程，这个 worker 的通道上预取数是 4
    worker_prefetch_multiplier=1,
    # 硬时限：一个任务超过 25 分钟，Celery 直接杀掉这个子进程（只防整个进程冻住）。
    # 被杀后库里的 attempt 还是 running，但没人续租了，租约过期后由巡检判为失败
    task_time_limit=25 * 60,
    task_ignore_result=True,  # 结果写在 PG 里，不需要 Celery 再存一份
    # 除了任务队列，worker 还会给自己建两个临时队列：远程控制（celery inspect 等命令）和事件广播。
    # 它们默认“不持久、不独占”，RabbitMQ 4 起禁止这种队列（实测启动即报 transient_nonexcl_queues）。
    # 改成独占（exclusive）：只属于建它的那条连接，连接断开自动删除——本来就是临时队列该有的样子
    control_queue_exclusive=True,
    event_queue_exclusive=True,
)


def worker_id() -> str:
    """租约的持有者名：主机名 + 进程号，日志里能看出是哪个容器的哪个子进程。"""
    return f"{socket.gethostname()}:{os.getpid()}"


@cache
def _components() -> tuple[Corpus, HybridRetriever]:
    """语料和检索器：每个子进程第一次执行任务时加载一次（约 2 秒），之后复用。"""
    return build_retriever(get_settings())


@cache
def _llm(model: str) -> ChatModel:
    return build_llm(get_settings(), model)


@cache
def _live_redis() -> redis.Redis:
    """发实时片段用的 Redis 客户端：每个子进程一个（内部是连接池，第一次 publish 时才连）。"""
    return make_redis(get_settings().redis_url)


def _describe(exc: Exception) -> str:
    """写进数据库、会展示给用户的错误说明。不用 str(exc)：httpx 的报错里带网关地址。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"llm_http_{exc.response.status_code}"
    return type(exc).__name__


def execute(engine: Engine, corpus: Corpus, retriever: HybridRetriever,
            make_llm: Callable[[str], ChatModel], live_redis: redis.Redis, attempt_id: int,
            worker: str) -> None:
    """执行一个 attempt。拆成普通函数（而不是直接写在 Celery 任务里），测试可以直接调用。"""
    held = lease.claim(engine, attempt_id, worker)
    if held is None:
        log.info("attempt %s 已被领取或已结束，跳过（重复投递）", attempt_id)
        return

    # 引用编号按这次执行里的步骤顺序累计（citations.py）。
    # M4 做“接管后从断点继续”时，要先用已经写进库的步骤把它恢复出来
    numbers = CitationNumbers()
    with lease.LeaseKeeper(engine, held):
        try:
            trace = run_episode(
                held.task, make_llm(held.model), build_registry(held.task, corpus, retriever),
                # 走流式调用：思考片段一到就 publish 到 Redis，前端逐字显示（live.py）；
                # 流式下“60 秒收不到数据就放弃”也才能对每一块生效
                on_delta=DeltaPublisher(live_redis, held.run_id, held.attempt_id, held.epoch),
                on_step=lambda step: lease.commit_step(
                    engine, held, step, numbers.number(step.tool_name, step.result)),
            )
        except lease.LeaseLost:
            # 执行权已被取代：什么都不再写，直接退出
            log.warning("attempt %s 执行权已被取代，停止执行", attempt_id)
            return
        except Exception as exc:
            # 网关报错、超时等：这次执行判为失败，用户可以点“重新生成”（M4 加自动重试）
            log.exception("attempt %s 执行失败", attempt_id)
            lease.fail(engine, held, _describe(exc))
            return
        lease.finish(engine, held, trace.final, trace.violation)
    log.info("attempt %s 完成：%s", attempt_id, trace.violation or trace.final)


@celery_app.task(name="margin.execute_attempt")
def execute_attempt(attempt_id: int) -> None:
    corpus, retriever = _components()
    execute(get_engine(), corpus, retriever, _llm, _live_redis(), attempt_id, worker_id())
