"""M2.5-1：给已有的 step 事件补上引用编号 citation_no

Revision ID: 0003
Revises: 0002

为什么要这次迁移：引用编号从前端移到后端（src/margin/citations.py），新的 step 事件带 citation_no，
前端不再自己编号。迁移之前写下的 step 事件没有这个字段，不补的话，打开旧题时来源卡片全部消失。

这是一次“数据迁移”：表结构不变，只改已有行的内容（events.payload 是 JSONB）。
按 run、seq 顺序读出所有 step 事件，每次执行（attempt_id）单独编号，再写回去。

编号规则在这里复制了一份，而不是 import margin.citations：迁移文件写好后就不再改，
应用代码以后可能会变；如果迁移依赖应用代码，几个月后在新库上从头跑迁移，结果可能和当初不一样，甚至跑不起来。
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def _key(payload: dict) -> tuple | None:
    result = payload.get("result") or {}
    data = result.get("data") or {}
    if payload.get("tool_name") != "cite" or not result.get("ok") or not data.get("grounded"):
        return None
    return data["doc_id"], data["block_id"], data["matched_text"]


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, payload FROM events WHERE type = 'step' ORDER BY run_id, seq")).all()
    seen: dict[int, list[tuple]] = {}  # attempt_id -> 已编号的原文
    for event_id, payload in rows:
        keys = seen.setdefault(payload["attempt_id"], [])
        key = _key(payload)
        number = None
        if key is not None and key not in keys:
            keys.append(key)
            number = len(keys)
        conn.execute(sa.text("UPDATE events SET payload = CAST(:payload AS jsonb) WHERE id = :id"),
                     {"id": event_id, "payload": json.dumps({**payload, "citation_no": number})})


def downgrade() -> None:
    # JSONB 的 - 运算符：删掉对象里的一个键
    op.execute("UPDATE events SET payload = payload - 'citation_no' WHERE type = 'step'")
