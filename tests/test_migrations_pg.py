"""数据迁移的集成测试（真实 PostgreSQL）：已有数据在升级后仍然正确。

表结构迁移由 conftest 的 engine 夹具覆盖（每次测试会话从空库执行全部迁移）；
但空库测不到“改已有行”的数据迁移，这里先写入旧格式的数据，再退回、重新升级。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from margin import runs

pytestmark = pytest.mark.integration

OWNER = 1


def old_step(attempt_id, step_no, tool_name, data=None, ok=True):
    """M2.5-1 之前的 step 事件：没有 citation_no。"""
    return {"attempt_id": attempt_id, "step_no": step_no, "tool_name": tool_name,
            "arguments": "{}", "result": {"ok": ok, "data": data or {}}, "reasoning": None}


def grounded(text):
    return {"doc_id": "jia_2023", "block_id": "jia_2023_b0001", "matched_text": text,
            "grounded": True}


def test_upgrade_numbers_citations_in_existing_step_events(db):
    """升级前写下的题：迁移 0003 给旧的 step 事件补上引用编号，打开旧题时来源卡片不会消失。

    每次执行（attempt）单独编号；第二次执行（重新生成）从 1 重新开始。
    """
    run_id, _ = runs.create_run(db, OWNER, "k1", "甲公司2023年营业收入是多少亿元？", None, "num",
                                "DeepSeek")
    with Session(db) as session, session.begin():
        for payload in [
            old_step(1, 0, "search_docs"),
            old_step(1, 1, "cite", grounded("营业收入120.5亿元")),
            old_step(1, 2, "cite", grounded("营业收入120.5亿元")),
            old_step(1, 3, "cite", grounded("同比增长12.4%")),
            old_step(2, 0, "cite", grounded("同比增长12.4%")),
        ]:
            runs.add_event(session, run_id, "step", payload)

    config = Config(Path(__file__).parents[1] / "alembic.ini")
    config.attributes["database_url"] = db.url.render_as_string(hide_password=False)
    command.downgrade(config, "0002")
    command.upgrade(config, "head")

    events, _ = runs.events_after(db, OWNER, run_id, 0)
    numbers = [(e.payload["attempt_id"], e.payload["citation_no"])
               for e in events if e.type == "step"]
    assert numbers == [(1, None), (1, 1), (1, None), (1, 2), (2, 1)]
