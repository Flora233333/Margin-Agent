"""M1.6 收尾：算空位、找重发的查询不再随历史变慢；删掉没用的 ix_outbox_sent_at

Revision ID: 0007
Revises: 0006

为什么要这次迁移（M1.6 review 发现）：dispatcher 每次投递前算空位（dispatcher.free_slots），
其中“已发出、还没被领取”= outbox 是 sent、attempt 还是 pending；
巡检找要重发的消息（resend_lost）也是这个条件。
做完的题 outbox 永远停在 sent、attempt 也不删，原来这两张表都只能整表扫描，题越多越慢。
    - 部分索引 attempts(id) WHERE status='pending'：只收录还没被领取的 attempt，
      正常时只有刚提交的几条，查询从这几条出发，不管历史多少都很小
      （和 ix_attempts_running_lease 同一个思路）。
    - 普通索引 outbox(attempt_id)：从 pending 的 attempt 找它那一行 outbox。
      原有的 ux_outbox_pending_attempt 只收录 pending 的行，找 sent 的用不上。
    - 删 ix_outbox_sent_at（0002 建的）：原来给对账的水位线查询用，
      M1.6 删掉水位线后没有查询用到它（取舍 22）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.create_index("ix_attempts_pending", "attempts", ["id"],
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index("ix_outbox_attempt_id", "outbox", ["attempt_id"])
    op.drop_index("ix_outbox_sent_at", table_name="outbox")


def downgrade() -> None:
    op.create_index("ix_outbox_sent_at", "outbox", ["sent_at"],
                    postgresql_where=sa.text("status = 'sent'"))
    op.drop_index("ix_outbox_attempt_id", table_name="outbox")
    op.drop_index("ix_attempts_pending", table_name="attempts")
