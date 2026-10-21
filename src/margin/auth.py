"""账号与登录：密码哈希、建账号 / 改密码、登录状态（session）。方案见 PLAN §5.10（M3a）、D30。

密码为什么用 argon2 而不是 SHA-256：
    SHA-256 很快，一张显卡每秒能算几十亿次。库泄露后，攻击者拿常见密码表逐个算哈希比对，
    弱密码几秒就被猜中。
    argon2 是专门给密码设计的“慢哈希”：每算一次要约几十毫秒、占 64MB 内存（参数在哈希串里），
    显卡并行也快不了多少。每次哈希还自动加一段随机的盐（salt），同一个密码两次哈希结果不同，
    攻击者没法提前算好一张“密码 → 哈希”的大表。
    哈希串长这样：$argon2id$v=19$m=65536,t=3,p=4$<盐>$<哈希>，算法和参数都在里面，验证时按它重算。

登录状态（session）怎么记：
    登录成功 → 生成一个 32 字节的随机令牌，放进浏览器的 cookie（api.py 设置），
    库里 sessions 表存它的 SHA-256。
    之后每个请求浏览器自动带上 cookie → 算哈希、查表 → 知道是哪个用户（resolve_session）。
    和 JWT 的区别：令牌本身不含任何信息，只是一把“钥匙”，真正的状态在服务端的表里，
    所以退出、改密码时删掉那一行，令牌立刻作废（JWT 签发后在过期前都有效，吊销要另做黑名单）。
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from .models import LoginSession, User

MIN_PASSWORD_LENGTH = 8
# 用户名只允许字母、数字和 . _ -：会出现在页面、日志和限流的 Redis 键里，不要空格和奇怪字符
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,32}")

SESSION_TTL = timedelta(days=7)  # 登录有效期；用户在用就顺延
RENEW_EVERY = timedelta(days=1)  # 距上次顺延超过 1 天才顺延（才写一次库），不是每个请求都写

# 默认参数（argon2id，t=3、m=64MB、p=4）是 RFC 9106 推荐的低内存配置，本机一次约 30 毫秒
_hasher = PasswordHasher()


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str


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


@cache
def _dummy_hash() -> str:
    return hash_password("用户不存在时拿来空算一次")


def authenticate(engine: Engine, username: str, password: str) -> CurrentUser | None:
    """用户名和密码都对，返回这个用户；否则 None（不区分是哪个不对）。

    用户不存在、或者还没设密码（迁移前的 dev）时，也拿一个假哈希验证一次再返回：
    否则“用户不存在”约 1 毫秒就返回、“密码不对”要 30 毫秒，别人量一量响应时间就知道哪些用户名存在。
    """
    with Session(engine) as session:
        row = session.execute(
            select(User.id, User.password_hash).where(User.username == username)).first()
    if row is None or row.password_hash is None:
        verify_password(_dummy_hash(), password)
        return None
    if not verify_password(row.password_hash, password):
        return None
    return CurrentUser(row.id, username)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(engine: Engine, user_id: int) -> str:
    """登录成功后调用：生成令牌、存它的哈希，返回令牌本身（只出现这一次，放进 cookie）。

    secrets.token_urlsafe(32)：操作系统提供的密码学安全随机数，32 字节（256 位），
    编码成 43 个 URL 安全字符；不能用 random 模块（它的输出能被推算出来）。
    同一事务里顺手删掉这个用户已经过期的 session：不然每登录一次多一行，过期的永远留在表里。
    """
    token = secrets.token_urlsafe(32)
    with Session(engine) as session, session.begin():
        session.execute(delete(LoginSession).where(
            LoginSession.user_id == user_id, LoginSession.expires_at <= func.now()))
        session.add(LoginSession(user_id=user_id, token_hash=_token_hash(token),
                                 expires_at=func.now() + SESSION_TTL))
    return token


def resolve_session(engine: Engine, token: str) -> tuple[CurrentUser, bool] | None:
    """cookie 里的令牌 → (当前用户, 这次是否顺延了有效期)；令牌不存在或已过期返回 None。

    顺延：有效期 7 天，剩余不到 6 天（= 上次顺延已经超过 1 天）才把过期时间改成“现在 + 7 天”。
    每个请求都写一次库太浪费（打开一道题就有好几个请求），一天顺延一次足够“在用就不过期”。
    顺延了要告诉调用方：cookie 自己也有 7 天的寿命，要重新下发一次，
    不然 7 天后浏览器把 cookie 删了。
    时间一律用数据库的 now()，不用 Python 的本机时间，免得两边时钟不一致。
    """
    with Session(engine) as session, session.begin():
        row = session.execute(
            select(LoginSession.id, LoginSession.expires_at, User.id.label("user_id"),
                   User.username, func.now().label("now"))
            .join(User, User.id == LoginSession.user_id)
            .where(LoginSession.token_hash == _token_hash(token),
                   LoginSession.expires_at > func.now())
        ).first()
        if row is None:
            return None
        renew = row.expires_at - row.now < SESSION_TTL - RENEW_EVERY
        if renew:
            session.execute(update(LoginSession).where(LoginSession.id == row.id)
                            .values(expires_at=func.now() + SESSION_TTL))
        return CurrentUser(row.user_id, row.username), renew


def delete_session(engine: Engine, token: str) -> None:
    """退出：删掉这一行，这个令牌立刻作废（别人拿到旧 cookie 也用不了）。"""
    with Session(engine) as session, session.begin():
        session.execute(delete(LoginSession).where(LoginSession.token_hash == _token_hash(token)))
