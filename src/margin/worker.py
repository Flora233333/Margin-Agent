"""后台 worker：从 Redis 队列收到 attempt_id，领取执行权，运行 Harness，每一步写进数据库。

启动（Linux / 容器里；Celery 的多进程模式不支持 Windows）：
    celery -A margin.worker worker --concurrency 4 --loglevel INFO
--concurrency 4 = 4 个子进程，每个同一时间只执行一个任务。

Celery 是什么：Python 的后台任务框架。dispatcher 用它往 Redis 里放一条消息
“执行 attempt 12”，worker 进程从 Redis 取消息、调用下面的 execute_attempt(12)。
队列只负责“叫醒 worker”；这个 attempt 该不该由我执行，由数据库里的租约决定（lease.py）。
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from functools import cache

import httpx
from celery import Celery
from sqlalchemy import Engine

from . import lease
from .assembly import build_llm, build_retriever
from .db import get_engine
from .harness import build_registry, run_episode
from .llm import ChatModel
from .retrieval import Corpus, HybridRetriever
from .settings import get_settings

log = logging.getLogger(__name__)

celery_app = Celery("margin", broker=get_settings().redis_url)
celery_app.conf.update(
    # 任务执行完才确认（ack）消息。默认是“取到就确认”：worker 进程取到后崩溃，消息就丢了
    task_acks_late=True,
    # 每个子进程只预取 1 条：手上只有正在执行的那一条，不会把别的任务压在自己这里排队
    worker_prefetch_multiplier=1,
    # 消息被取走后 2 小时还没确认，Redis 会把它重新交给别的 worker（要大于任务最长执行时间）
    broker_transport_options={"visibility_timeout": 2 * 3600},
    # 硬时限：一个任务超过 25 分钟，Celery 直接杀掉这个子进程（只防整个进程冻住）。
    # 被杀后库里的 attempt 还是 running，但没人续租了，租约过期后由巡检判为失败
    task_time_limit=25 * 60,
    task_ignore_result=True,  # 结果写在 PG 里，不需要 Celery 再存一份
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


def _describe(exc: Exception) -> str:
    """写进数据库、会展示给用户的错误说明。不用 str(exc)：httpx 的报错里带网关地址。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"llm_http_{exc.response.status_code}"
    return type(exc).__name__


def execute(engine: Engine, corpus: Corpus, retriever: HybridRetriever,
            make_llm: Callable[[str], ChatModel], attempt_id: int, worker: str) -> None:
    """执行一个 attempt。拆成普通函数（而不是直接写在 Celery 任务里），测试可以直接调用。"""
    held = lease.claim(engine, attempt_id, worker)
    if held is None:
        log.info("attempt %s 已被领取或已结束，跳过（重复投递）", attempt_id)
        return

    with lease.LeaseKeeper(engine, held):
        try:
            trace = run_episode(
                held.task, make_llm(held.model), build_registry(held.task, corpus, retriever),
                # M1 还不推送思考片段（M2 推到 Redis）；传一个空回调是为了走流式调用，
                # 流式下“60 秒收不到数据就放弃”才能对每一块生效
                on_delta=lambda turn, kind, text: None,
                on_step=lambda step: lease.commit_step(engine, held, step),
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
    execute(get_engine(), corpus, retriever, _llm, attempt_id, worker_id())
