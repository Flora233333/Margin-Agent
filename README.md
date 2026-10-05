# Margin

**可追溯证据的金融长文档 Agent 服务。** 名字取双关：页边（margin）批注证据，也是金融里的保证金/利润率。

用户对年报、债券募集说明书、保险条款等长文档提问（跨文档、跨年度、需要计算），
Agent 自己检索、阅读原文、核对口径、计算，并给每个结论标注原文出处。

> 当前阶段：M1.5 完成。提交 → RabbitMQ → 后台 worker 执行 → 每一步落库 → SSE 推送的服务闭环已跑通，消息丢失能由巡检对账补回；前端在 M2。进度见 [STATUS.md](STATUS.md)。

## 结构

```text
src/margin/
  harness/      Agent 核心（移植自 RC6-C）：提示词、十个工具、主循环
  retrieval/    检索：BM25 + 实体别名 + 向量（pgvector，可选），RRF 融合
  llm/          OpenAI 兼容模型客户端（DeepSeek / vLLM 自训模型）
  api.py        HTTP 接口（FastAPI）：提交、查询、重新生成、SSE 事件流
  runs.py       建任务（Outbox + 幂等）、查询、事件
  lease.py      租约 + epoch：领取、带 epoch 提交、续租、巡检
  worker.py     Celery worker：执行 Harness，每一步写库
  dispatcher.py 把 outbox 投递到 RabbitMQ（LISTEN / NOTIFY 唤醒）；租约巡检、消息丢失对账
  models.py     数据库表；结构变更走 migrations/（Alembic）
  settings.py   配置（MARGIN_* 环境变量 / .env）
tests/          pytest，假模型 FakeLLM 驱动，不调用真实模型；-m integration 连真实 PG
scripts/        手动运行脚本（真实语料 + 真实模型）
web/design/     前端设计稿（配色、字体、组件样式）
docs/           计划、决策记录、移植说明
```

## 开发

```bash
conda env create -f environment.yml                         # 建 conda 环境 margin（Python 3.11）
conda run -n margin python -m pip install -e . --group dev  # 安装项目和开发工具
conda run -n margin pytest                                  # 运行测试
conda run -n margin ruff check src tests scripts            # 代码检查
```

用真实语料跑一道题：复制 `.env.example` 为 `.env` 并填写，然后

```bash
conda run -n margin --no-capture-output python scripts/run_episode.py "甲公司2023年营业收入同比增长多少？" --format pct --model DeepSeek
```

## 启动整套服务

```bash
# 在 WSL 的 Docker 里起 8 个服务：postgres、rabbitmq、redis、embedding、migrate（迁移后退出）、api、worker、dispatcher
wsl -d Ubuntu-22.04 -- bash -lc "cd /mnt/d/competition/margin-agent && docker compose up -d --build --wait"
# 只需一次：把 RC6-C 的向量导入 PG（表由迁移建好）
conda run -n margin --no-capture-output python scripts/import_vectors.py .cache/chroma/rc6-local-qwen3emb06b-q8_0-v2/chroma
```

提交一道题、看事件流（接口文档：http://127.0.0.1:8000/docs）：

```bash
curl -X POST http://127.0.0.1:8000/runs -H "Content-Type: application/json" -H "Idempotency-Key: demo-1" \
     -d '{"question": "广晟控股2023年营业收入是多少亿元？", "answer_format": "num"}'
curl -N http://127.0.0.1:8000/runs/1/events      # SSE：每完成一步推一条，结束后关闭
curl http://127.0.0.1:8000/runs/1                # 完整记录
```

- embedding 模型 `models/Qwen3-Embedding-0.6B-Q8_0.gguf` 不进仓库，需要自己放进去（compose 只读挂载给容器）。
- RabbitMQ 管理界面：http://127.0.0.1:15672（用户 `margin`，密码是 `.env` 的 `MARGIN_MQ_PASSWORD`，默认 `margin_dev`），
  能看到任务队列 `celery` 的排队数、消费者数和预取数。
- 集成测试：`conda run -n margin pytest -m integration`（会新建测试库 margin_test，不碰开发库；只需要 PG）。
- WSL 空闲时会自动关机，服务随之停止；跑长任务时保持一个 WSL 终端开着。

## 文档

- [docs/PLAN.md](docs/PLAN.md)：后端完整计划（技术栈、架构、里程碑）
- [docs/DECISIONS.md](docs/DECISIONS.md)：技术决策记录
- [docs/MIGRATION.md](docs/MIGRATION.md)：RC6-C 移植说明（保留了什么、简化了什么）
- [AGENTS.md](AGENTS.md)：给 AI 编程助手的工作约定
