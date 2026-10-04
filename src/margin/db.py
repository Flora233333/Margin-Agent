"""数据库连接：SQLAlchemy Engine。

Engine 是“连接工厂 + 连接池”：程序向它借连接，用完归还，而不是每次都新建 TCP 连接
（新建一次要几毫秒到几十毫秒，还占 PG 的进程资源）。一个进程只建一个 Engine。

注意多进程：Celery worker 是 fork 出来的子进程，连接（socket）不能跨进程共用，
所以 Engine 在第一次用到时才创建（get_engine），每个子进程各建各的。
"""

from __future__ import annotations

from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from .settings import get_settings

# 任何一条 SQL 超过 30 秒由 PG 主动取消（报错），不会无限占着连接和锁
STATEMENT_TIMEOUT_MS = 30_000


def make_engine(database_url: str, pool_size: int = 5) -> Engine:
    # 配置里的地址是 postgresql://...（psql、psycopg 都认识）；
    # SQLAlchemy 要在协议名里写明用哪个驱动，这里统一用 psycopg 3
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    return create_engine(
        url,
        pool_size=pool_size,
        # 借出连接前先 ping 一下：PG 重启过（例如 WSL 空闲关机后被拉起），池里的旧连接已断，
        # ping 失败就换一条新连接，而不是让业务 SQL 报错
        pool_pre_ping=True,
        # options 是 libpq 的连接参数，-c 设置会话级配置，等于每条连接建立时执行一次 SET
        connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
    )


@cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)
