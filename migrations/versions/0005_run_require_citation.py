"""M2.5-3：runs 加 require_citation（产品模式的引用提醒开关）

Revision ID: 0005
Revises: 0004

为什么要这次迁移（PLAN §5.8 ②）：产品里的题没有引用就交答案时，finalize 提醒一次；
评测回放和自训模型对照实验要保持原来的规则（不提醒），结果才能和以前比较。
这是提交时决定的、这道题的属性，重新生成也要沿用，所以存在 runs 上。
已有的行按产品题处理（默认 true）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column("runs", sa.Column("require_citation", sa.Boolean, nullable=False,
                                    server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("runs", "require_citation")
