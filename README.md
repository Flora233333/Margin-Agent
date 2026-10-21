# Margin

**可追溯证据的金融长文档 Agent 服务。** 名字取双关：页边（margin）批注证据，也是金融里的保证金/利润率。

用户对年报、债券募集说明书、保险条款等长文档提问（跨文档、跨年度、需要计算），
Agent 自己检索、阅读原文、核对口径、计算，并给每个结论标注原文出处。

> 当前阶段：M3a 完成。提交 → RabbitMQ → 后台 worker 执行 → 每一步落库 → SSE 推送的服务闭环已跑通；worker 有空位才投递，消息丢失由巡检按时间重发；React 前端能看到思考逐字出现、每一步的工具调用、带原文旁注的回答；账号密码登录，每个人只看得到自己的题。下一步 M3b（模型并发上限、配额、轮流投递）。进度见 [STATUS.md](STATUS.md)。

## 结构

```text
src/margin/
  harness/      Agent 核心（移植自 RC6-C）：提示词、十个工具、主循环
  retrieval/    检索：BM25 + 实体别名 + 向量（pgvector，可选），RRF 融合
  llm/          OpenAI 兼容模型客户端（DeepSeek / vLLM 自训模型）
  api.py        HTTP 接口（FastAPI）：登录、提交、查询、历史列表、重新生成、SSE 事件流；检查 Origin
  auth.py       账号和登录：argon2 密码、服务端 session（cookie 里放令牌，库里只存哈希）
  ratelimit.py  登录限流：Redis 滑动窗口（Lua）
  event_hub.py  API 进程里的 PG LISTEN：有新事件时叫醒对应的 SSE 连接
  live.py       实时思考片段：worker 发到 Redis pub/sub，API 转发给 SSE
  runs.py       建任务（Outbox + 幂等）、查询、事件
  lease.py      租约 + epoch：领取、带 epoch 提交、续租、巡检
  worker.py     Celery worker：执行 Harness，每一步写库
  dispatcher.py worker 有空位才把 outbox 投递到 RabbitMQ（LISTEN / NOTIFY 唤醒）；租约巡检、重发丢失的消息
  models.py     数据库表；结构变更走 migrations/（Alembic）
  settings.py   配置（MARGIN_* 环境变量 / .env）
tests/          pytest，假模型 FakeLLM 驱动，不调用真实模型；-m integration 连真实 PG
scripts/        手动运行脚本（真实语料 + 真实模型）
web/app/        前端（Vite + React + TypeScript）
web/design/     前端设计稿（配色、字体、组件样式；本地打开 preview.html）
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

建账号（没有注册页；`dev` 是迁移预置的开发用户，设了密码才能登录，以前的题都归它）：

```bash
# 按提示输两遍密码（至少 8 位，不显示）；对已有的用户名再执行一次就是改密码
conda run -n margin --no-capture-output python scripts/create_user.py dev
```

登录、提交一道题、看事件流（接口文档：http://127.0.0.1:8000/docs）。改数据的请求要带允许的 `Origin`（防跨站伪造），
登录后的请求带上 cookie：

```bash
O='Origin: http://localhost:5173'
curl -c jar -H "$O" -H "Content-Type: application/json" -X POST http://127.0.0.1:8000/auth/login \
     --data-binary @login.json                   # {"username": "dev", "password": "..."}；用完删掉这个文件
curl -b jar -H "$O" -X POST http://127.0.0.1:8000/runs -H "Content-Type: application/json" -H "Idempotency-Key: demo-1" \
     -d '{"question": "广晟控股2023年营业收入是多少亿元？", "answer_format": "num"}'
curl -b jar -N http://127.0.0.1:8000/runs/1/events   # SSE：每完成一步推一条，结束后关闭
curl -b jar http://127.0.0.1:8000/runs/1             # 完整记录
```

- embedding 模型 `models/Qwen3-Embedding-0.6B-Q8_0.gguf` 不进仓库，需要自己放进去（compose 只读挂载给容器）。
- RabbitMQ 管理界面：http://127.0.0.1:15672（用户 `margin`，密码是 `.env` 的 `MARGIN_MQ_PASSWORD`，默认 `margin_dev`），
  能看到任务队列 `celery` 的排队数、消费者数和预取数。
- 集成测试：`conda run -n margin pytest -m integration`（会新建测试库 margin_test，不碰开发库；需要 PG 和 Redis）。
- WSL 空闲时会自动关机，服务随之停止；跑长任务时保持一个 WSL 终端开着。

打开前端（服务起好之后）：

```bash
cd web/app
npm install        # 第一次；依赖只装在 web/app/node_modules
npm run dev        # http://localhost:5173 （先登录），/api 由 Vite 转发到 127.0.0.1:8000
npm test           # vitest
```

## 文档

- [docs/PLAN.md](docs/PLAN.md)：后端完整计划（技术栈、架构、里程碑）
- [docs/DECISIONS.md](docs/DECISIONS.md)：技术决策记录
- [docs/MIGRATION.md](docs/MIGRATION.md)：RC6-C 移植说明（保留了什么、简化了什么）
- [AGENTS.md](AGENTS.md)：给 AI 编程助手的工作约定
