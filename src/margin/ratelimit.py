"""限流：Redis 有序集合 + Lua 脚本实现的滑动窗口。现在用来防暴力猜密码（PLAN §5.10 M3a）。

滑动窗口是什么：规则“10 分钟最多 10 次”，在任何一个时刻往回看 10 分钟，数这段时间里有几次。
和“固定窗口”（每个整 10 分钟清零一次）比：固定窗口在 9:59 和 10:01 各来 10 次，
两分钟里来了 20 次也不拦；滑动窗口始终只看“最近 10 分钟”，没有这个缝。

怎么存：每个“用户名 + IP”一个有序集合（sorted set），每次尝试存一个成员，分数 = 尝试时刻（毫秒）。
    数“最近 10 分钟有几次” = 先删掉分数早于“现在 − 10 分钟”的成员，再数剩下几个。

为什么要 Lua 脚本：“删过期的 → 数一数 → 没超就记一次”是三条命令。如果分开发，
两个请求可能同时数到 9、都认为没超、都记一次，变成 11 次。Redis 执行 Lua 脚本时
不会穿插执行别的命令（单线程，一个脚本整个执行完才轮到下一个），三步在一个脚本里，
就不会被别的请求插进来——这就是“原子性”。
时间也在脚本里用 Redis 自己的 TIME 取：多个 API 进程的本机时钟可能不一致，只认 Redis 一个时钟。
"""

from __future__ import annotations

import math
import secrets

import redis

# KEYS[1]：这个“用户名 + IP”的有序集合
# ARGV[1]：窗口长度（毫秒）  ARGV[2]：上限
# ARGV[3]：这一次尝试的成员名（随机串，同一毫秒的两次也不会合并成一个）
# 返回 0 = 放行（已记下这一次）；返回正数 = 不放行，还要等这么多毫秒（窗口里最早那次滑出去的时刻）
SLIDING_WINDOW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return tonumber(oldest[2]) + window - now
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return 0
"""


class SlidingWindowLimiter:
    """每个键在任意 window_seconds 秒内最多放行 limit 次。"""

    def __init__(self, client: redis.Redis, limit: int, window_seconds: int) -> None:
        self.client = client
        self.limit = limit
        self.window_ms = window_seconds * 1000
        # register_script：第一次执行时把脚本发给 Redis 并记下它的 SHA1，之后只发 SHA1（EVALSHA），
        # Redis 重启丢了缓存的脚本时自动重发一次
        self.script = client.register_script(SLIDING_WINDOW)

    def hit(self, key: str) -> int:
        """尝试一次：放行返回 0（并计入窗口），不放行返回还要等几秒（向上取整，至少 1）。"""
        wait_ms = self.script(keys=[key], args=[self.window_ms, self.limit, secrets.token_hex(8)])
        return math.ceil(wait_ms / 1000)

    def reset(self, key: str) -> None:
        self.client.delete(key)
