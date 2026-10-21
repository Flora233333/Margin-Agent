"""M3a 登录：users 加密码哈希，新表 sessions（登录状态）

Revision ID: 0008
Revises: 0007

- users.password_hash：argon2 哈希（auth.hash_password）。可以为空：迁移前就有的开发用户 dev（id=1）
  还没有密码，设上之前登录不了（auth.authenticate 把空哈希当作“密码不对”）；旧题仍归它，用
  scripts/create_user.py dev 设密码后继续用。
- sessions：一行 = 一次登录。浏览器 cookie 里放随机令牌，这里只存令牌的 SHA-256（token_hash），
  库被拖走也拿不到能用的令牌。expires_at 是过期时间，用户在用就往后顺延（auth.resolve_session）。
  user_id 的索引：改密码时删掉这个用户的全部 session、登录时顺手清掉这个用户已过期的 session，都按它查。
  ON DELETE CASCADE：删用户时它的 session 一起删（session 没有单独存在的意义）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.Text))
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_column("users", "password_hash")
