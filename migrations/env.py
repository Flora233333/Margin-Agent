"""Alembic 执行迁移的入口：连上数据库，按顺序执行 versions/ 里还没执行过的迁移。

用法（先起 PG）：conda run -n margin alembic upgrade head
数据库地址不写在 alembic.ini 里，而是读配置 MARGIN_DATABASE_URL（和应用同一个来源）。

数据库里有一张 alembic_version 表，记着当前执行到了哪个版本，所以重复运行 upgrade 是安全的。
"""

from alembic import context

from margin.db import make_engine
from margin.models import Base
from margin.settings import get_settings

# 集成测试会把测试库的地址放进 config.attributes；平时读配置
url = context.config.attributes.get("database_url") or get_settings().database_url
engine = make_engine(url, pool_size=1)
with engine.connect() as connection:
    # target_metadata：`alembic check` 用它对比“模型里定义的表”和“数据库里真实的表”是否一致
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():  # 整个迁移在一个事务里，中途失败全部回滚
        context.run_migrations()
engine.dispose()
