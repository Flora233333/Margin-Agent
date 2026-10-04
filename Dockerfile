# api / worker / dispatcher / migrate 共用的应用镜像（同一份代码，启动命令不同，见 compose.yaml）。
# 不用 conda：官方 Python 镜像 + pyproject 的依赖，和 CI 一致。
FROM python:3.11-slim

# 不写 .pyc；print / 日志不缓冲，docker compose logs 能实时看到
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
# 缓存挂载：下载过的 wheel 留在构建缓存里，改代码重新构建时不用再下载一遍依赖
RUN --mount=type=cache,target=/root/.cache/pip pip install .

COPY alembic.ini ./
COPY migrations ./migrations
