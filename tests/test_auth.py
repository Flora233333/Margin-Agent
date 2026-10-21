"""账号的单元测试：密码哈希、建账号的输入检查（不连数据库）。"""

import pytest

from margin.auth import check_new_account, hash_password, verify_password


def test_password_hash_is_salted_and_only_the_right_password_verifies():
    """库里存的不是密码本身；同一个密码两次哈希不同（带随机盐，没法用预先算好的表反查）；
    只有原密码能通过验证。"""
    first, second = hash_password("correct horse"), hash_password("correct horse")

    assert first.startswith("$argon2id$") and "correct horse" not in first
    assert first != second
    assert verify_password(first, "correct horse") and verify_password(second, "correct horse")
    assert not verify_password(first, "correct hors")


@pytest.mark.parametrize(("username", "password"), [
    ("test_a", "short"),  # 太短的密码
    ("有中文", "long enough"),  # 用户名会进 Redis 键和日志，只允许字母数字 . _ -
    ("with space", "long enough"),
    ("", "long enough"),
    ("a" * 33, "long enough"),
])
def test_bad_new_account_is_rejected_with_a_reason(username, password):
    assert check_new_account(username, password)


def test_valid_new_account_passes():
    assert check_new_account("test_a", "long enough") is None
