"""M2.5-2：runs 加标题和结论说明；答案格式改为可空（空 = 由“理解题目”自动判断）

Revision ID: 0004
Revises: 0003

为什么要这次迁移（PLAN §5.8 ①）：用户不再手选答案格式，由同一个模型读题后判断，顺带写出
左栏显示的标题（title）和结论旁边的说明（answer_label）。
    - title、answer_label：可空。空表示还没理解完（或理解失败前的旧数据），前端显示问题原句。
    - answer_format：从 NOT NULL 改为可空。提交时不带格式的题先写空，理解完回填。
      评测回放仍然带格式提交，不受影响；已有的行都有值，改约束不改数据。
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column("runs", sa.Column("title", sa.Text))
    op.add_column("runs", sa.Column("answer_label", sa.Text))
    op.alter_column("runs", "answer_format", nullable=True)


def downgrade() -> None:
    # 退回前要求每行都有格式：理解失败的题按“文本”补上
    op.execute("UPDATE runs SET answer_format = 'text' WHERE answer_format IS NULL")
    op.alter_column("runs", "answer_format", nullable=False)
    op.drop_column("runs", "answer_label")
    op.drop_column("runs", "title")
