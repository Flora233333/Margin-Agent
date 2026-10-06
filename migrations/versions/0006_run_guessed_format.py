"""M2.5：答案格式分成“调用方给定的”和“理解题目猜的”两列

Revision ID: 0006
Revises: 0005

为什么要这次迁移：0004 让理解题目把猜的格式回填进 answer_format，和评测回放给定的格式混在了一起。
给定的格式要严格校验（评测按它打分）；猜的格式可能猜错（run 66：“有什么时间要求”被猜成日期，
模型交的规定原文被 finalize 退回 4 次，最后被逼着推出两个日期交上去）。
猜的格式只用来规范化和显示，交的答案对不上时按文本收下（worker.py）。两者必须分开存，
否则重新生成时分不清哪个是猜的，又会按猜的格式硬卡。
    - answer_format：只放调用方给定的格式；产品里的题为空，不再回填。
    - guessed_format：理解题目猜的格式。
已有数据：有标题的题都是 M2.5 之后在页面上提交的（没有给定格式），它们的 answer_format 是猜的，挪到 guessed_format。
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.add_column("runs", sa.Column("guessed_format", sa.Text))
    op.execute("UPDATE runs SET guessed_format = answer_format, answer_format = NULL "
               "WHERE title IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE runs SET answer_format = guessed_format "
               "WHERE answer_format IS NULL AND guessed_format IS NOT NULL")
    op.drop_column("runs", "guessed_format")
