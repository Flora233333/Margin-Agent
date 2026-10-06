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
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from typing import Any

import redis
from celery import Celery
from sqlalchemy import Engine

from . import lease
from .assembly import build_llm, build_retriever
from .citations import CitationNumbers
from .compose import compose
from .db import get_engine
from .harness import TOOLS, build_registry, run_episode
from .harness.tools.base import ToolError
from .live import DeltaPublisher, make_redis
from .llm import ChatModel, describe_error
from .retrieval import Corpus, HybridRetriever
from .settings import get_settings
from .understand import understand

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


def _understand(engine: Engine, held: lease.Lease, llm: ChatModel) -> str:
    """理解题目并写库（在另一个线程里和 Harness 并行），返回猜的答案格式。"""
    understood = understand(llm, held.task["question"])
    try:
        lease.save_understanding(engine, held, understood)
    except lease.LeaseLost:
        pass  # 执行权已被取代：标题不写了；主线程下一次提交步骤时也会发现并停止
    return understood.answer_format


def _tools_with_guessed_format(guess: Callable[[], str]) -> dict[str, Any]:
    """产品里的题没有给定格式：finalize 换成“按猜的格式试，对不上就按文本收下”，其余工具不变。

    猜的格式来自理解题目。第一次执行时理解题目在另一个线程里，guess() 等它的结果
    （平均 6 秒，Harness 到 finalize 通常已经过了一两分钟，几乎不用真的等）；重新生成时用库里存的。
    猜错时不能退回让模型改：主流程的模型看不到格式，只看到“没法按 date 规范化”，
    会去猜系统要什么、把对的答案改成别的形式（run 66 被退回 4 次，最后推出两个日期交上去）。
    所以对不上就按文本收下，结果里记下 answer_format_fallback（猜的是什么），页面按文本显示。
    """
    finalize, args_model = TOOLS["finalize"]

    def finalize_with_guess(ctx: Any, args: Any) -> dict[str, Any]:
        guessed = guess()
        ctx.state.task["answer_format"] = guessed
        try:
            return finalize(ctx, args)
        except ToolError as exc:
            if exc.code != "answer_format_invalid":
                raise  # 没搜索过、没引用（提醒）等别的前置条件照常退回
        ctx.state.task["answer_format"] = "text"
        return {**finalize(ctx, args), "answer_format_fallback": guessed}

    return {**TOOLS, "finalize": (finalize_with_guess, args_model)}


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
    llm = make_llm(held.model)
    # 退出 with 时先等理解题目的线程结束，再停止续租：那个线程写库也要校验租约
    with lease.LeaseKeeper(engine, held), ThreadPoolExecutor(max_workers=1) as pool:
        tools = TOOLS
        understood = None
        # 第一次执行才理解题目；重新生成时标题已经有了，不再调用
        if held.needs_understanding:
            understood = pool.submit(_understand, engine, held, llm)
        # 提交时没给答案格式（产品里的题）才按猜的格式“软校验”；评测回放带着格式，finalize 严格校验
        if held.task["answer_format"] is None:
            guessed = held.guessed_format or "text"
            tools = _tools_with_guessed_format(understood.result if understood else lambda: guessed)
        publish = DeltaPublisher(live_redis, held.run_id, held.attempt_id, held.epoch)
        try:
            trace = run_episode(
                held.task, llm, build_registry(held.task, corpus, retriever, tools),
                # 走流式调用：思考片段一到就 publish 到 Redis，前端逐字显示（live.py）；
                # 流式下“60 秒收不到数据就放弃”也才能对每一块生效
                on_delta=publish,
                on_step=lambda step: lease.commit_step(
                    engine, held, step, numbers.number(step.tool_name, step.result)),
            )
            written = None
            # 交了答案或放弃作答才写回答；触发停止条件（violation）时没有可写的结论
            if trace.final is not None:
                turn = len(trace.steps)  # 回答的片段排在最后一步之后
                written = compose(llm, held.task["question"], trace.final, trace.steps,
                                  on_text=lambda text: publish(turn, "answer", text))
            if understood is not None:
                understood.result()  # 让 run_understood 事件排在 attempt_finished 前面
        except lease.LeaseLost:
            # 执行权已被取代：什么都不再写，直接退出
            log.warning("attempt %s 执行权已被取代，停止执行", attempt_id)
            return
        except Exception as exc:
            # 网关报错、超时等：这次执行判为失败，用户可以点“重新生成”（M4 加自动重试）
            log.exception("attempt %s 执行失败", attempt_id)
            lease.fail(engine, held, describe_error(exc))
            return
        lease.finish(engine, held, trace.final, trace.violation, written)
    log.info("attempt %s 完成：%s", attempt_id, trace.violation or trace.final)


@celery_app.task(name="margin.execute_attempt")
def execute_attempt(attempt_id: int) -> None:
    corpus, retriever = _components()
    execute(get_engine(), corpus, retriever, _llm, _live_redis(), attempt_id, worker_id())
