"""M1 初始表：users / runs / attempts / steps / events / outbox / block_vectors

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None

TIMESTAMP = sa.DateTime(timezone=True)  # timestamptz：带时区的时间，统一按 UTC 存


def upgrade() -> None:
    # pgvector 是 PG 扩展，每个数据库启用一次之后才有 vector 类型
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
    )
    # M1 还没有登录，所有请求都算作这个开发用户（M3 加登录）
    op.execute("INSERT INTO users (id, username) VALUES (1, 'dev')")
    # 手动指定了 id=1，要把自增序列推到 1 之后，否则以后插入新用户会再拿到 1 而冲突
    op.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), 1)")

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("options", JSONB),
        sa.Column("answer_format", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("last_seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "idempotency_key"),
    )

    op.create_table(
        "attempts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("lease_owner", sa.Text),
        sa.Column("lease_epoch", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lease_until", TIMESTAMP),
        sa.Column("last_step", sa.Integer),
        sa.Column("final", JSONB),
        sa.Column("violation", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TIMESTAMP),
        sa.Column("finished_at", TIMESTAMP),
        sa.UniqueConstraint("run_id", "attempt_no"),
    )
    op.create_index("ix_attempts_running_lease", "attempts", ["lease_until"],
                    postgresql_where=sa.text("status = 'running'"))

    op.create_table(
        "steps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("attempt_id", sa.Integer, sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("arguments", sa.Text, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("reasoning", sa.Text),
        sa.Column("llm_ms", sa.Integer, nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("attempt_id", "step_no"),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "seq"),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("attempt_id", sa.Integer, sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("tries", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", TIMESTAMP),
    )
    op.create_index("ux_outbox_pending_attempt", "outbox", ["attempt_id"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))

    # 原来由 scripts/import_vectors.py 建表，现在统一由迁移建；导入脚本只负责灌数据
    op.create_table(
        "block_vectors",
        sa.Column("block_id", sa.Text, primary_key=True),
        sa.Column("doc_id", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
    )


def downgrade() -> None:
    for table in ["block_vectors", "outbox", "events", "steps", "attempts", "runs", "users"]:
        op.drop_table(table)
