"""登录限流的集成测试：滑动窗口（真实 Redis）和登录接口的 429（真实 PostgreSQL + Redis）。

默认不跑；运行：conda run -n margin pytest -m integration
"""

import threading
import time

import pytest
from conftest import ORIGIN, PASSWORD
from fastapi.testclient import TestClient

from margin.api import app, get_login_limiter
from margin.auth import set_password
from margin.live import make_redis
from margin.ratelimit import SlidingWindowLimiter
from margin.settings import get_settings

pytestmark = pytest.mark.integration

WRONG = {"username": "test_a", "password": "wrong password"}
RIGHT = {"username": "test_a", "password": PASSWORD}


@pytest.fixture
def redis_client():
    client = make_redis(get_settings().redis_url)
    yield client
    for key in client.scan_iter("test:ratelimit:*"):
        client.delete(key)
    client.close()


def test_concurrent_attempts_never_get_past_the_limit(redis_client):
    """30 个请求同时来抢 10 个名额：恰好放行 10 个。“删过期 → 计数 → 记一次”如果分成三条命令，
    几个请求会同时数到 9、都放行；Lua 脚本在 Redis 里整段执行，中间插不进别的请求。"""
    limiter = SlidingWindowLimiter(redis_client, limit=10, window_seconds=60)
    results = []
    barrier = threading.Barrier(30)

    def attempt():
        barrier.wait()  # 30 个线程在同一刻开始
        results.append(limiter.hit("test:ratelimit:race"))

    threads = [threading.Thread(target=attempt) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(0) == 10
    assert all(0 < wait <= 60 for wait in results if wait)


def test_window_slides_instead_of_resetting_all_at_once(redis_client):
    """滑动窗口：窗口 2 秒、上限 2 次。第 0 秒和第 1 秒各试一次，第 1.2 秒被拦；
    到第 2.5 秒，只有第 0 秒那次滑出去了，放行一次，紧接着又被拦
    （第 1 秒那次还在窗口里，到第 3 秒才出去）。
    每个判断离边界都留 0.4 秒以上：这里用本机时钟睡，窗口按 Redis 的时钟算，两个时钟走得不一样快——
    本机 Redis 在 WSL 虚拟机里，实测比 Windows 慢约 4%（睡 2.1 秒 Redis 只过 2.015 秒），
    原来只留 0.1 秒时偶尔失败。"""
    limiter = SlidingWindowLimiter(redis_client, limit=2, window_seconds=2)
    key = "test:ratelimit:slide"

    assert limiter.hit(key) == 0
    time.sleep(1.0)
    assert limiter.hit(key) == 0
    time.sleep(0.2)
    assert limiter.hit(key) == 1  # 还要等约 0.8 秒，向上取整为 1
    time.sleep(1.3)
    assert limiter.hit(key) == 0
    assert limiter.hit(key) > 0


def test_eleventh_login_attempt_gets_429_even_with_the_right_password(db, make_client):
    """连续输错 10 次（都是 401），第 11 次 429 并带 Retry-After；这时就算密码对了也不放行，
    否则攻击者照样可以一直猜下去（实验 A5 的自动化版本）。"""
    client = make_client(login=False)
    set_password(db, "test_a", PASSWORD)

    statuses = [client.post("/auth/login", json=WRONG).status_code for _ in range(10)]
    blocked = client.post("/auth/login", json=RIGHT)

    assert statuses == [401] * 10
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "尝试次数太多，请 10 分钟后再试"}
    assert 590 <= int(blocked.headers["retry-after"]) <= 600


def test_limit_counts_each_username_and_ip_separately(db, make_client):
    """test_a 在这个 IP 上被锁住，不影响 test_b，也不影响 test_a 从别的 IP 登录。"""
    client = make_client(login=False)
    set_password(db, "test_a", PASSWORD)
    set_password(db, "test_b", PASSWORD)
    for _ in range(10):
        client.post("/auth/login", json=WRONG)

    other_user = client.post("/auth/login", json={"username": "test_b", "password": PASSWORD})
    elsewhere = TestClient(app, headers={"Origin": ORIGIN}, client=("10.0.0.2", 50000))

    assert client.post("/auth/login", json=RIGHT).status_code == 429
    assert other_user.status_code == 200
    assert elsewhere.post("/auth/login", json=RIGHT).status_code == 200


def test_successful_login_clears_earlier_failures(db, make_client):
    """输错 9 次后登录成功：之前的次数清零，之后再输错还能试满 10 次才被拦。"""
    client = make_client(login=False)
    set_password(db, "test_a", PASSWORD)
    for _ in range(9):
        client.post("/auth/login", json=WRONG)
    assert client.post("/auth/login", json=RIGHT).status_code == 200

    statuses = [client.post("/auth/login", json=WRONG).status_code for _ in range(11)]

    assert statuses == [401] * 10 + [429]


def test_login_is_refused_when_redis_is_down(db, make_client):
    """Redis 挂了、限流失效：不放行（503），而不是不限次数地让人猜；
    已登录的人照常使用（session 在 PG）。"""
    client = make_client()  # dev 先登录好
    set_password(db, "test_a", PASSWORD)
    app.dependency_overrides[get_login_limiter] = lambda: SlidingWindowLimiter(
        make_redis("redis://127.0.0.1:1/0"), 10, 600)

    response = client.post("/auth/login", json=RIGHT)

    assert (response.status_code, response.json()) == (503, {"detail": "暂时无法登录，请稍后再试"})
    assert client.get("/auth/me").json() == {"username": "dev"}
