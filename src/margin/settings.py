"""配置：所有 MARGIN_ 开头的环境变量集中读进一个有类型的 Settings 对象。

取值优先级：真实环境变量 > .env 文件 > 这里写的默认值。
本机开发时值来自 .env；容器里由 compose 的 environment 传入（路径、主机名和本机不同）。

为什么用 pydantic-settings 而不是到处 os.environ[...]：
    - 缺了必填项，启动时就报错并指出是哪一项，而不是跑到一半才 KeyError；
    - 类型自动转换（字符串 -> Path / int），密钥用 SecretStr，打印时显示成 **********。
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_prefix：字段 llm_model 对应环境变量 MARGIN_LLM_MODEL（不区分大小写）。
    # extra="ignore"：.env 里还有只给 compose 用的变量（如 MARGIN_PG_PASSWORD），这里用不到。
    # hide_input_in_errors：配置校验失败时，报错信息里不带输入值，密钥不会出现在日志里。
    model_config = SettingsConfigDict(
        env_prefix="MARGIN_", env_file=".env", env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True,
    )

    # ---- 模型（OpenAI 兼容接口）----
    llm_base_url: str
    llm_model: str = "DeepSeek"  # 默认模型（学校网关主用）；脚本可用 --model 临时换
    llm_api_key: SecretStr = SecretStr("")
    # 自训模型（实验室服务器上的 vLLM）：模型名 -> 地址列表，一张卡一个副本一个地址。
    # 不在表里的模型走上面的网关。
    # 环境变量写 JSON，例如 MARGIN_LOCAL_MODELS={"C2": ["http://127.0.0.1:18080/v1", "http://127.0.0.1:18081/v1"]}
    local_models: dict[str, list[str]] = {}

    # ---- 语料（只读）----
    blocks_path: Path
    manifest_path: Path
    alias_path: Path
    cache_dir: Path = Path(".cache")  # BM25 索引缓存放在 cache_dir/bm25

    # ---- 基础服务 ----
    database_url: str
    # Celery 的任务队列（RabbitMQ，D19）。地址末尾的 // 表示默认的虚拟主机 "/"
    # （RabbitMQ 用虚拟主机隔离不同应用的队列，地址里的 "/" 要写成 %2F 或 //）
    broker_url: str = "amqp://margin:margin_dev@127.0.0.1:5672//"
    redis_url: str = "redis://127.0.0.1:6379/0"
    # 每个 worker 容器的子进程数（= 最多同时执行几道题）。compose 里 worker 的 --concurrency
    # 和 dispatcher 读的都是这一个变量：dispatcher 按“消费者数 × 它”算有几个空位（M1.6，有空位才发）
    worker_concurrency: int = 4
    embedding_base_url: str = ""  # 留空 = 不开向量检索，只用 BM25 + 别名两路
    embedding_model: str = "rc6-local-qwen3emb06b-q8_0-v2"

    # ---- 登录（M3a）----
    # 改数据的请求只接受这些来源（Origin 头，api.check_origin）：网页从哪个地址打开就填哪个。
    # 开发时是 Vite 的地址；环境变量写 JSON，例如 MARGIN_ALLOWED_ORIGINS=["https://margin.example.com"]
    allowed_origins: list[str] = ["http://localhost:5173"]
    # session cookie 只走 HTTPS（Secure 属性）。开发是 http，打开了浏览器就不发 cookie；
    # M6 上 HTTPS 后设 true
    cookie_secure: bool = False
    # 防暴力猜密码：同一个“用户名 + IP”在任意 login_window_seconds 秒内
    # 最多尝试 login_max_attempts 次
    login_max_attempts: int = 10
    login_window_seconds: int = 600


@cache
def get_settings() -> Settings:
    """整个进程只读一次配置。"""
    return Settings()
