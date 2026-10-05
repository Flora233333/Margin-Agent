"""API 进程里的“有新事件”通知中心：一条 LISTEN 连接，把通知转给关注这个 run 的 SSE 连接（D20）。

为什么不让每个 SSE 连接各自 LISTEN：每条 LISTEN 都要独占一条 PG 连接，100 个人同时看进度
就占 100 条，很快把 PG 的连接数用完。所以每个 API 进程只开一条，收到“run 12 有新事件”后
在内存里叫醒正在看 run 12 的那几个 SSE 连接，由它们自己按 seq 去库里取。

为什么用一个后台线程 + 同步 psycopg，而不是 asyncio 版的连接：psycopg 的异步连接在 Windows 默认的
事件循环（Proactor）上不能用，开发时的 uvicorn 和测试都跑在 Windows 上。线程里收到通知后，
用 loop.call_soon_threadsafe 把“叫醒”交回给 SSE 所在的事件循环执行——
asyncio 的对象不能跨线程直接操作。

通知不持久：LISTEN 连接断开重连的那几秒里发出的通知会丢。所以 SSE 仍保留低频兜底查询（api.py）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from .runs import RUN_EVENTS_CHANNEL

log = logging.getLogger(__name__)

RECONNECT_SECONDS = 2  # LISTEN 连接断开后隔多久重连
STOP_CHECK_SECONDS = 1  # 等通知时每隔多久看一眼“是否该停了”
NEW_EVENTS = "new_events"  # 放进 inbox 的标记：“库里有新事件了，去查一下”


class EventHub:
    """用法：hub.start() 启动后台线程；SSE 里 `with hub.watch(run_id) as inbox:`，
    之后 `await inbox.get()` 拿到 NEW_EVENTS 就去查库。
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        # run_id -> 正在等这个 run 的 (事件循环, 收件箱)；一个 run 可以有多个人同时在看
        self._watchers: dict[int, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        # _watchers 会被两个线程访问（SSE 所在的事件循环线程增删，监听线程读取），用锁保护
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._listen_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    @contextmanager
    def watch(self, run_id: int) -> Iterator[asyncio.Queue]:
        """登记“我在看这个 run”，返回这个连接的收件箱：有新事件时会收到一个 NEW_EVENTS。

        用队列而不是 asyncio.Event：SSE 还要等实时片段（live.forward_deltas 也往同一个收件箱里放），
        两种消息进同一个队列，SSE 只需要 await 一个 inbox.get()。离开 with 时自动注销。
        """
        entry = (asyncio.get_running_loop(), asyncio.Queue())
        with self._lock:
            self._watchers.setdefault(run_id, set()).add(entry)
        try:
            yield entry[1]
        finally:
            with self._lock:
                watchers = self._watchers[run_id]
                watchers.discard(entry)
                if not watchers:
                    del self._watchers[run_id]

    def _wake(self, run_id: int) -> None:
        with self._lock:
            entries = list(self._watchers.get(run_id, ()))
        for loop, inbox in entries:
            loop.call_soon_threadsafe(inbox.put_nowait, NEW_EVENTS)

    def _listen_forever(self) -> None:
        while not self._stop.is_set():
            try:
                # 自动提交：PG 只在连接不处于未提交事务时才把通知交出来（同 dispatcher.listen）
                with psycopg.connect(self.database_url, autocommit=True) as conn:
                    conn.execute(f"LISTEN {RUN_EVENTS_CHANNEL}")
                    log.info("事件通知：已开始 LISTEN %s", RUN_EVENTS_CHANNEL)
                    while not self._stop.is_set():
                        for notify in conn.notifies(timeout=STOP_CHECK_SECONDS):
                            self._wake(int(notify.payload))
            except psycopg.OperationalError:
                # PG 重启、网络断开：API 不能因此退出（还在服务别的请求），隔 2 秒重连；
                # 断开期间 SSE 靠兜底查询，最多晚 10 秒
                log.exception("事件通知的 LISTEN 连接断开，%s 秒后重连", RECONNECT_SECONDS)
                self._stop.wait(RECONNECT_SECONDS)
