"""账号的集成测试（真实 PostgreSQL）：建账号、改密码。

默认不跑；运行：conda run -n margin pytest -m integration
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from margin import runs
from margin.auth import set_password, verify_password
from margin.models import LoginSession, Run, User

pytestmark = pytest.mark.integration


def password_hash_of(db, username):
    with Session(db) as session:
        return session.scalar(select(User.password_hash).where(User.username == username))


def test_setting_dev_password_keeps_its_id_and_old_runs(db):
    """迁移前的题都归开发用户 dev（id=1）：给它设密码走“改密码”，不能新建一个 dev，旧题不能丢。"""
    runs.create_run(db, 1, "k1", "甲公司2023年营业收入是多少亿元？", None, None, "DeepSeek")

    assert password_hash_of(db, "dev") is None  # 迁移后还没有密码，登录不了
    assert set_password(db, "dev", "dev password 1") == (1, False)

    assert verify_password(password_hash_of(db, "dev"), "dev password 1")
    with Session(db) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(Run.owner_id)) == 1


def test_new_username_creates_an_account(db):
    user_id, created = set_password(db, "test_a", "password a")

    assert created and user_id != 1
    assert verify_password(password_hash_of(db, "test_a"), "password a")


def test_changing_password_logs_out_existing_sessions(db):
    """怀疑密码泄露才会改密码：已经登录着的（可能是别人）必须立刻失效，只留旧密码作废是不够的。"""
    user_id, _ = set_password(db, "test_a", "old password")
    other_id, _ = set_password(db, "test_b", "password b")
    expires = datetime.now(UTC) + timedelta(days=7)
    with Session(db) as session, session.begin():
        session.add_all([LoginSession(user_id=user_id, token_hash="a1", expires_at=expires),
                         LoginSession(user_id=other_id, token_hash="b1", expires_at=expires)])

    set_password(db, "test_a", "new password")

    assert not verify_password(password_hash_of(db, "test_a"), "old password")
    with Session(db) as session:
        assert session.scalars(select(LoginSession.token_hash)).all() == ["b1"]  # 别人的不受影响
