"""登录的接口测试：登录、退出、/auth/me、session 过期和顺延（真实 PostgreSQL）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

import hashlib

import pytest
from conftest import PASSWORD
from sqlalchemy import text

from margin.api import SESSION_COOKIE
from margin.auth import set_password

pytestmark = pytest.mark.integration


def login(client, username, password=PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


def session_rows(db):
    with db.connect() as conn:
        return conn.execute(text(
            "SELECT token_hash, expires_at - now() AS left FROM sessions ORDER BY id")).all()


def set_expiry(db, interval):
    with db.begin() as conn:
        conn.execute(text(f"UPDATE sessions SET expires_at = now() + interval '{interval}'"))


def test_login_sets_httponly_cookie_and_stores_only_its_hash(db, make_client):
    """登录成功：cookie 带 HttpOnly（页面脚本读不到）、7 天有效；
    库里只有令牌的 SHA-256，没有令牌本身。"""
    client = make_client(login=False)
    set_password(db, "test_a", PASSWORD)

    response = login(client, "test_a")

    assert response.status_code == 200
    assert response.json() == {"username": "test_a"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "Max-Age=604800" in cookie and "Path=/" in cookie
    token = response.cookies[SESSION_COOKIE]
    assert [r.token_hash for r in session_rows(db)] == [hashlib.sha256(token.encode()).hexdigest()]
    assert client.get("/auth/me").json() == {"username": "test_a"}


@pytest.mark.parametrize(("username", "password"), [
    ("test_a", "wrong password"),  # 密码不对
    ("nobody", PASSWORD),  # 没有这个用户
    ("dev", PASSWORD),  # 迁移前就有的 dev，还没设密码
])
def test_failed_login_gets_the_same_401_and_no_cookie(db, make_client, username, password):
    """三种失败对外完全一样：同一个状态码、同一句话，不能据此试出哪些用户名存在。"""
    set_password(db, "test_a", PASSWORD)
    client = make_client(login=False)

    response = login(client, username, password)

    assert (response.status_code, response.json()) == (401, {"detail": "用户名或密码不对"})
    assert "set-cookie" not in response.headers
    assert session_rows(db) == []


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/auth/me"), ("GET", "/runs"), ("POST", "/runs"), ("GET", "/runs/1"),
    ("POST", "/runs/1/regenerate"), ("GET", "/runs/1/events"),
])
@pytest.mark.parametrize("cookie", [None, "made-up-token"])
def test_requests_without_a_valid_session_get_401(make_client, method, path, cookie):
    """没登录、或者 cookie 是编的：除了登录 / 退出，每个接口都是 401（前端收到就跳登录页）。"""
    client = make_client(login=False)
    if cookie:
        client.cookies.set(SESSION_COOKIE, cookie)

    response = client.request(method, path, json={"question": "q"},
                              headers={"Idempotency-Key": "k1"})

    assert response.status_code == 401


def test_logout_makes_the_old_cookie_useless(db, make_client):
    """退出后 session 被删：就算有人事先抄下了 cookie，再拿来用也是 401（实验 A2 的自动化版本）。"""
    client = make_client("test_a")
    token = client.cookies[SESSION_COOKIE]

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert f'{SESSION_COOKIE}=""' in response.headers["set-cookie"]  # 让浏览器删掉 cookie
    assert session_rows(db) == []
    replay = make_client(login=False)
    replay.cookies.set(SESSION_COOKIE, token)
    assert replay.get("/auth/me").status_code == 401


def test_logout_without_session_still_succeeds(make_client):
    """重复点退出、cookie 早已过期：都当成功，前端不用处理错误。"""
    assert make_client(login=False).post("/auth/logout").status_code == 204


def test_expired_session_gets_401_and_next_login_cleans_it_up(db, make_client):
    client = make_client("test_a")
    set_expiry(db, "-1 second")

    assert client.get("/auth/me").status_code == 401
    assert len(session_rows(db)) == 1  # 过期的行还在，下次登录时清掉

    assert login(client, "test_a").status_code == 200
    rows = session_rows(db)
    assert len(rows) == 1 and rows[0].left.days == 6  # 只剩新的这一条（还剩 6 天 23 小时多）


def test_session_is_renewed_at_most_once_a_day(db, make_client):
    """在用就顺延，但一天最多写一次库：剩 5 天时访问 → 顺延回 7 天并重发 cookie；
    紧接着再访问 → 不再写、不重发。"""
    client = make_client("test_a")
    set_expiry(db, "5 days")

    renewed = client.get("/runs")
    again = client.get("/runs")

    assert "Max-Age=604800" in renewed.headers["set-cookie"]
    assert "set-cookie" not in again.headers
    assert session_rows(db)[0].left.days == 6
