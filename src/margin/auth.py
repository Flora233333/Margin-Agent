"""账号与登录：密码哈希、建账号 / 改密码。方案见 PLAN §5.10（M3a）、D30。

密码为什么用 argon2 而不是 SHA-256：
    SHA-256 很快，一张显卡每秒能算几十亿次。库泄露后，攻击者拿常见密码表逐个算哈希比对，
    弱密码几秒就被猜中。
    argon2 是专门给密码设计的“慢哈希”：每算一次要约几十毫秒、占 64MB 内存（参数在哈希串里），
    显卡并行也快不了多少。每次哈希还自动加一段随机的盐（salt），同一个密码两次哈希结果不同，
    攻击者没法提前算好一张“密码 → 哈希”的大表。
    哈希串长这样：$argon2id$v=19$m=65536,t=3,p=4$<盐>$<哈希>，算法和参数都在里面，验证时按它重算。
"""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session

from .models import LoginSession, User

MIN_PASSWORD_LENGTH = 8
# 用户名只允许字母、数字和 . _ -：会出现在页面、日志和限流的 Redis 键里，不要空格和奇怪字符
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,32}")

# 默认参数（argon2id，t=3、m=64MB、p=4）是 RFC 9106 推荐的低内存配置，本机一次约 30 毫秒
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def check_new_account(username: str, password: str) -> str | None:
    """建账号脚本的输入检查：有问题返回一句说明，没问题返回 None。"""
    if not USERNAME_PATTERN.fullmatch(username):
        return "用户名只能用字母、数字和 . _ -，最长 32 个字符"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"密码至少 {MIN_PASSWORD_LENGTH} 个字符"
    return None


def set_password(engine: Engine, username: str, password: str) -> tuple[int, bool]:
    """建账号或改密码：用户名不存在就新建，存在就换密码。返回 (用户 id, 是否新建)。

    开发用户 dev（迁移 0001 预置，id=1）走“改密码”这条路：id 不变，旧题仍归它。
    改密码时同一事务删掉这个用户的全部 session：怀疑密码泄露才会改，
    已经登录着的（可能是别人）要立刻失效。
    """
    password_hash = hash_password(password)
    with Session(engine) as session, session.begin():
        user_id = session.scalar(
            update(User).where(User.username == username).values(password_hash=password_hash)
            .returning(User.id))
        if user_id is not None:
            session.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
            return user_id, False
        user = User(username=username, password_hash=password_hash)
        session.add(user)
        session.flush()  # 执行 INSERT，拿到自增的 id
        return user.id, True
