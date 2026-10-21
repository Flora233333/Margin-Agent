"""测试共用的数据：一个只有 3 个文档的小语料，和一个按剧本回放回复的假模型；
以及集成测试用的测试数据库。

为什么用假模型（FakeLLM）：测试要快、免费、结果每次一样。真实模型每次回答都不同，
没法写断言；我们要测的是 Harness 的逻辑（压缩、停止条件、工具规则），而不是模型聪不聪明。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from margin.api import app, get_db, get_hub
from margin.auth import set_password
from margin.db import make_engine
from margin.event_hub import EventHub
from margin.harness import build_registry
from margin.live import make_redis
from margin.retrieval import Corpus, HybridRetriever
from margin.retrieval.alias import Alias, AliasCatalog
from margin.settings import get_settings

BLOCKS = [
    {
        "doc_id": "jia_2023", "block_id": "jia_2023_b0001", "char_start": 0,
        "section_path": ["第三节 管理层讨论与分析"], "has_table": False,
        "text": "2023年，甲公司实现营业收入120.5亿元，同比增长12.4%。\n"
                "归属于母公司股东的净利润8.2亿元。\n研发投入3.1亿元。",
    },
    {
        "doc_id": "jia_2023", "block_id": "jia_2023_b0002", "char_start": 100,
        "section_path": ["第十节 财务报告", "合并资产负债表"], "has_table": True,
        "text": "| 项目 | 2023年末 | 2022年末 |\n"
                "| 货币资金 | 35.6 | 30.1 |\n| 总资产 | 410.2 | 388.7 |",
    },
    {
        "doc_id": "jia_2022", "block_id": "jia_2022_b0001", "char_start": 0,
        "section_path": ["第三节 管理层讨论与分析"], "has_table": False,
        "text": "2022年，甲公司实现营业收入107.2亿元。\n归属于母公司股东的净利润7.5亿元。",
    },
    {
        "doc_id": "yi_bond", "block_id": "yi_bond_b0001", "char_start": 0,
        "section_path": ["募集说明书", "本期债券基本条款"], "has_table": False,
        "text": "本期债券简称为22乙债01，债券代码185001。\n票面利率为3.15%，期限5年。",
    },
]
TITLES = {"jia_2023": "甲公司2023年年度报告", "jia_2022": "甲公司2022年年度报告",
          "yi_bond": "乙公司2022年公开发行公司债券募集说明书"}


@pytest.fixture(scope="session")
def corpus() -> Corpus:
    return Corpus(BLOCKS, TITLES)


@pytest.fixture(scope="session")
def retriever(corpus: Corpus) -> HybridRetriever:
    aliases = AliasCatalog([
        Alias("yi_bond", "22乙债01", "22乙债01", "security_code", "yi_bond_b0001"),
        Alias("yi_bond", "185001", "185001", "security_code", "yi_bond_b0001"),
    ])
    return HybridRetriever.create(corpus, aliases=aliases)


@pytest.fixture
def make_registry(corpus, retriever):
    """为一道题创建新的工具集（每次都是全新状态）。"""
    def make(task: dict[str, Any] | None = None):
        return build_registry(task or {"question": "测试题", "answer_format": "num"},
                              corpus, retriever)
    return make


# ---------------------------------------------------------------- 集成测试的数据库

TEST_DB = "margin_test"


@pytest.fixture(scope="session")
def engine():
    """每次测试会话重建独立的测试库 margin_test 并执行全部迁移，不碰开发库里的数据。

    迁移同时被测试到了：迁移脚本有错，所有集成测试都会失败。
    """
    admin_url = get_settings().database_url
    # CREATE / DROP DATABASE 不能在事务里执行，所以用 autocommit 连接
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    url = make_url(admin_url).set(database=TEST_DB).render_as_string(hide_password=False)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    config.attributes["database_url"] = url  # migrations/env.py 读这里
    command.upgrade(config, "head")
    engine = make_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def database_url(engine) -> str:
    """测试库的连接串（psycopg 直接用的格式），给需要专用连接的 LISTEN 用。"""
    return engine.url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def hub(database_url):
    """API 的事件通知中心，监听测试库。整个测试会话共用一个后台线程。"""
    hub = EventHub(database_url)
    hub.start()
    yield hub
    hub.stop()


@pytest.fixture
def db(engine):
    """每个测试开始前清空业务表（保留迁移预置的开发用户，并把它恢复成没有密码），测试之间互不影响。"""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE runs, attempts, steps, events, outbox, block_vectors, sessions"
                          " RESTART IDENTITY"))
        conn.execute(text("DELETE FROM users WHERE id <> 1"))
        conn.execute(text("UPDATE users SET password_hash = NULL"))
    return engine


PASSWORD = "test password"  # 接口测试里所有账号用同一个密码
ORIGIN = "http://localhost:5173"  # 网页的地址，默认配置的允许列表里只有它


@pytest.fixture
def make_client(db, hub):
    """接口测试：返回一个函数 make(用户名)，建好账号、登录，得到带着这个用户 cookie 的 TestClient。

    每个用户一个客户端、cookie 各存各的，像两个人各用各的浏览器。login=False 得到没登录的客户端。
    接口改用测试库，事件通知也监听测试库。TestClient 在进程内直接调用 app，不用真的起服务器。
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_hub] = lambda: hub

    def make(username: str = "dev", login: bool = True) -> TestClient:
        # 像浏览器一样每个请求都带 Origin（改数据的请求要检查它，api.check_origin）
        client = TestClient(app, headers={"Origin": ORIGIN})
        if login:
            set_password(db, username, PASSWORD)
            response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
            assert response.status_code == 200
        return client

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client) -> TestClient:
    """已登录的开发用户 dev（id=1）。"""
    return make_client()


@pytest.fixture(scope="session")
def live_redis():
    """worker 发实时片段用的 Redis 客户端（compose 的 redis，本机 6379）。"""
    client = make_redis(get_settings().redis_url)
    yield client
    client.close()
