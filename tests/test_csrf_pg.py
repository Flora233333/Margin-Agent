"""防跨站伪造请求的接口测试：Origin 检查、cookie 属性（真实 PostgreSQL）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

import pytest
from conftest import PASSWORD
from sqlalchemy import text

from margin.api import SESSION_COOKIE
from margin.auth import set_password
from margin.settings import get_settings

pytestmark = pytest.mark.integration

QUESTION = {"question": "甲公司2023年营业收入是多少亿元？"}


def run_count(db):
    with db.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM runs")).scalar_one()


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:5174", None])
def test_post_from_another_site_is_rejected_and_creates_nothing(db, client, origin):
    """登录着的用户被别的网站的页面借 cookie 发 POST：拒绝，库里不多一道题（实验 A4 的自动化版本）。
    端口不同也算别的来源；没带 Origin 的（不是浏览器发的）也拒绝。"""
    headers = {"Idempotency-Key": "k1", "Origin": origin}
    if origin is None:
        client.headers.pop("Origin")
        headers.pop("Origin")

    response = client.post("/runs", json=QUESTION, headers=headers)

    assert response.status_code == 403
    assert run_count(db) == 0


def test_every_kind_of_post_is_checked(db, client):
    """不只是提交：重新生成、登录（防止被悄悄登进攻击者的账号）、退出都检查。"""
    assert client.post("/runs", json=QUESTION, headers={"Idempotency-Key": "k1"}).status_code == 202
    evil = {"Origin": "https://evil.example"}

    assert client.post("/runs/1/regenerate", headers=evil).status_code == 403
    assert client.post("/auth/login", json={"username": "dev", "password": PASSWORD},
                       headers=evil).status_code == 403
    assert client.post("/auth/logout", headers=evil).status_code == 403
    assert client.get("/auth/me").status_code == 200  # 退出被拒，仍然登录着


def test_reads_are_not_checked(client):
    """GET 不改数据，不检查 Origin：别的网站读不到响应内容（同源策略），Lax 下也不带 cookie。"""
    assert client.get("/runs", headers={"Origin": "https://evil.example"}).status_code == 200


def test_session_cookie_is_samesite_lax_and_secure_only_when_configured(db, make_client,
                                                                         monkeypatch):
    """SameSite=Lax：别的网站发起的 POST 浏览器不带 cookie。Secure 由配置打开（M6 上 HTTPS 后），
    开发时是 http，打开了浏览器就不发 cookie、谁都登录不了。"""
    client = make_client(login=False)
    set_password(db, "test_a", PASSWORD)
    body = {"username": "test_a", "password": PASSWORD}

    plain = client.post("/auth/login", json=body).headers["set-cookie"]
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    secure = client.post("/auth/login", json=body).headers["set-cookie"]

    assert plain.startswith(f"{SESSION_COOKIE}=") and "SameSite=lax" in plain
    assert "Secure" not in plain
    assert "Secure" in secure
