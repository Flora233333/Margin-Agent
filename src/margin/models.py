"""数据库表（SQLAlchemy ORM 模型）。表结构的变更一律通过 Alembic 迁移（migrations/）执行。

几张表的关系：
    users ─< runs ─< attempts ─< steps
                │          └──< outbox
                └──< events
    block_vectors（向量检索用，和业务表无关）

    run      = 用户提交的一道题
    attempt  = 这道题的一次执行；用户点“重新生成”就新建一条，旧的保留
    step     = 一次执行里的一轮（模型调了哪个工具、参数、结果）
    event    = 给前端看的事件流，按 seq 编号，断线后按 seq 补发
    outbox   = “要把这个 attempt 投递给 Celery”的待办记录，见 dispatcher.py

状态取值（用普通文本列，取值写在注释里）：
    runs.status      queued / running / completed / failed（跟随最新一个 attempt）
    attempts.status  pending（等 worker 领取）/ running / completed（Harness 正常结束，
                     不一定答出了题，见 violation）/ failed（出错或租约过期）
    outbox.status    pending / sent
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Qwen3-Embedding-0.6B 的向量维度
EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    # Python 类型 -> 数据库列类型的默认映射：
    #   dict 存成 PG 的 JSONB（二进制 JSON，可建索引、可按字段查询）；时间一律带时区
    type_annotation_map = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


class User(Base):
    """M1 只有一个开发用户（迁移里预置 id=1）；M3 加密码哈希和登录。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Run(Base):
    __tablename__ = "runs"
    # 同一个用户的同一个幂等键只能有一个 run：重复提交时插入会撞上这个唯一约束
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any] | None]  # 选择题的选项 {"A": "...", ...}
    answer_format: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    # 这个 run 已分配到的最大事件序号。分配新序号 = 把它加 1（会锁住这一行），
    # 所以同一个 run 的事件序号按提交顺序连续递增，见 runs.add_event
    last_seq: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_no"),
        # 巡检要找“running 且租约已过期”的 attempt；部分索引只收录 running 的行，又小又快
        Index("ix_attempts_running_lease", "lease_until",
              postgresql_where=text("status = 'running'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    attempt_no: Mapped[int]  # 第几次执行：1、2、3……
    trigger: Mapped[str] = mapped_column(Text)  # submit（首次提交）/ regenerate（用户重新生成）
    model: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    # ---- 租约（见 lease.py）----
    lease_owner: Mapped[str | None] = mapped_column(Text)  # 哪个 worker 进程，如 "host:1234"
    lease_epoch: Mapped[int] = mapped_column(server_default="0")  # 第几代执行权，每次领取 +1
    lease_until: Mapped[datetime | None]  # 租约到期时间，worker 不断续租往后推
    last_step: Mapped[int | None]  # 最后一个已提交步骤的序号
    # ---- 结果 ----
    final: Mapped[dict[str, Any] | None]  # finalize / escalate 的结果
    violation: Mapped[str | None] = mapped_column(Text)  # Harness 的停止原因，正常提交为空
    error: Mapped[str | None] = mapped_column(Text)  # 执行出错（网关报错、超时、租约过期）
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class Step(Base):
    __tablename__ = "steps"
    # 同一个 attempt 的同一步只能写一次
    __table_args__ = (UniqueConstraint("attempt_id", "step_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))
    step_no: Mapped[int]  # = Harness 的轮次 turn
    tool_name: Mapped[str] = mapped_column(Text)
    arguments: Mapped[str] = mapped_column(Text)  # 模型给出的原始 JSON 字符串，原样保存
    result: Mapped[dict[str, Any]]
    reasoning: Mapped[str | None] = mapped_column(Text)
    llm_ms: Mapped[int]  # 这一轮模型调用耗时
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    seq: Mapped[int]  # 同一个 run 内从 1 开始连续编号，SSE 断线后按它补发
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        # 部分唯一索引：同一个 attempt 最多一条 pending 记录（已发送的不限）。
        # 以后巡检给“消息丢了”的 attempt 补投递时，重复补也只会有一条生效
        Index("ux_outbox_pending_attempt", "attempt_id", unique=True,
              postgresql_where=text("status = 'pending'")),
        # 对账的水位线查询按 sent_at 找最近投递的记录；只索引已发送的行（迁移 0002）
        Index("ix_outbox_sent_at", "sent_at", postgresql_where=text("status = 'sent'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    tries: Mapped[int] = mapped_column(server_default="0")  # 投递失败过几次（算退避时间用）
    # 对账判定“消息丢了”后补发过几次（改回 pending 重新投递）；到上限就把 attempt 判失败
    redeliveries: Mapped[int] = mapped_column(server_default="0")
    # 下次允许投递的时间：投递失败后往后推，Redis 挂了时不会每秒疯狂重试
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]


class BlockVector(Base):
    """每个文档块的向量，从 RC6-C 的 v2 索引导入（scripts/import_vectors.py）。"""

    __tablename__ = "block_vectors"

    block_id: Mapped[str] = mapped_column(Text, primary_key=True)
    doc_id: Mapped[str] = mapped_column(Text)
    # vector(1024)：pgvector 的列类型，维度写进类型里，维度不对的数据插不进去
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
