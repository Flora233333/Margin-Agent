"""实时片段（第二层事件）：模型逐字输出的思考，经 Redis pub/sub 从 worker 送到 API 的 SSE。

方案见 PLAN §5.4。

两层事件的分工：
    持久事件  每一步的完整结果，写 PG、带 seq，断线能按 seq 补发（runs.add_event）；
    实时片段  模型每吐出几个字就一条，量大、只为了“看着它在想”，不落库、不补发——
              这一步结束后，持久事件里的完整思考会把这些片段整体替换掉。
每个片段都写进 PG 会造成严重的写放大（一步几百上千条），所以走 Redis。

pub/sub（发布 / 订阅）：publish 到一个频道的消息，会立即送给此刻订阅了这个频道的所有连接；
没人订阅就直接丢弃，也不保存——正好符合“错过就算了”的语义。

片段里带 attempt_id 和 epoch：重新生成、或者（M4）别的 worker 接管之后，旧执行可能还有
几条片段在路上，前端只显示和当前执行（最近一次 attempt_started 的 attempt_id + epoch）一致的片段。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis
import redis.asyncio

log = logging.getLogger(__name__)

REDIS_TIMEOUT_SECONDS = 1  # Redis 卡住时最多等 1 秒，不能拖慢模型的流式输出


def channel(run_id: int) -> str:
    return f"run:{run_id}:live"


class DeltaPublisher:
    """worker 这一侧：把一次执行的思考片段 publish 出去。一次执行（attempt）一个实例。

    实时片段只是“锦上添花”：Redis 出问题时不能让这道题失败。所以第一次发送失败就记一条日志、
    这次执行后面的片段都不再发（不在每个片段上反复超时、刷屏），执行照常进行，
    每一步结束后的持久事件仍然会把完整思考送到前端。
    """

    def __init__(self, client: redis.Redis, run_id: int, attempt_id: int, epoch: int) -> None:
        self.client = client
        self.channel = channel(run_id)
        self.attempt_id = attempt_id
        self.epoch = epoch
        self.broken = False

    def __call__(self, turn: int, kind: str, text: str) -> None:
        """签名和 run_episode 的 on_delta 一致：(轮次, 类型 reasoning / content, 片段)。"""
        if self.broken:
            return
        message = json.dumps({"attempt_id": self.attempt_id, "epoch": self.epoch, "turn": turn,
                              "kind": kind, "text": text}, ensure_ascii=False)
        try:
            self.client.publish(self.channel, message)
        except redis.RedisError:
            self.broken = True
            log.warning("attempt %s 的实时片段发送失败，本次执行不再发送（不影响执行）",
                        self.attempt_id, exc_info=True)


def make_redis(redis_url: str) -> redis.Redis:
    return redis.Redis.from_url(redis_url, socket_timeout=REDIS_TIMEOUT_SECONDS,
                                socket_connect_timeout=REDIS_TIMEOUT_SECONDS)


@asynccontextmanager
async def forward_deltas(redis_url: str, run_id: int,
                         inbox: asyncio.Queue[Any]) -> AsyncIterator[None]:
    """API 这一侧：订阅这个 run 的频道，收到的片段（JSON 字符串）放进 inbox，直到离开 with。

    每个 SSE 连接各自建一个客户端、订阅一次（各占一条 Redis 连接）：Redis 的连接很便宜
    （默认上限 1 万），不像 PG 那样需要“每进程一条再分发”。用完就关，不跨请求共用，
    也就不用操心 asyncio 客户端绑定在哪个事件循环上。
    Redis 连不上时只记日志，SSE 照常推持久事件。
    """
    client = redis.asyncio.Redis.from_url(redis_url, socket_connect_timeout=REDIS_TIMEOUT_SECONDS)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel(run_id))
    except redis.RedisError:
        log.warning("订阅 run %s 的实时片段失败，只推持久事件", run_id, exc_info=True)
        await pubsub.aclose()
        await client.aclose()
        yield
        return

    async def pump() -> None:
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    inbox.put_nowait(message["data"].decode())
        except redis.RedisError:
            log.warning("run %s 的实时片段订阅断开，只推持久事件", run_id, exc_info=True)

    task = asyncio.create_task(pump())
    try:
        yield
    finally:
        task.cancel()
        await pubsub.aclose()
        await client.aclose()
