"""M1.5：outbox 加补发次数 redeliveries + 已发送记录的 sent_at 部分索引

Revision ID: 0002
Revises: 0001

为什么要这次迁移：巡检要能发现“消息在队列里丢了”的 attempt 并补发（PLAN §5.7）。
    - redeliveries：这条记录被对账补发过几次，到 3 次就不再补发、把 attempt 判失败。
      不复用 tries：tries 是“发送失败”的次数，用来算退避时间，两者含义不同。
    - 部分索引 outbox(sent_at) WHERE status='sent'：对账的水位线查询按 sent_at 找
      “最近 1 小时内投递的记录”，只索引已发送的行。

这是一次“增量”迁移：已经执行过 0001 的库只执行这一个文件，原有数据不动。
新加的列带默认值 0，PG 11 起加带常量默认值的列不需要重写整张表，旧行读出来就是 0。
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column("outbox", sa.Column("redeliveries", sa.Integer, nullable=False,
                                      server_default="0"))
    op.create_index("ix_outbox_sent_at", "outbox", ["sent_at"],
                    postgresql_where=sa.text("status = 'sent'"))


def downgrade() -> None:
    op.drop_index("ix_outbox_sent_at", table_name="outbox")
    op.drop_column("outbox", "redeliveries")
