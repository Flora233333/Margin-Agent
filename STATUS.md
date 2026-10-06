# 当前状态

更新：2026-10-06

## 所在阶段：M1.5、M2、M2 补完（A）完成（待作者 review）；M2.5 回答形态进行中（M2.5-0～5 完成，剩收尾）

计划全文见 [docs/PLAN.md](docs/PLAN.md)。

## 新会话交接（2026-10-06 写，给执行 M1.5 → M2 的会话）

> 已按本节完成 M1.5 和 M2（2026-10-06）。本节保留作为“提交方式”的参考；下一个会话从下方“下一步”开始，
> 先请作者 review M1.5 / M2 的提交并确认“执行中的取舍”。

**任务**：先做 M1.5（下方“下一步”表格的第 1～5 步），满足 M1.5 结束条件后，接着做 M2（“再下一步”）。
两个里程碑连续推进：每一步一个小提交，提交信息说清“做了什么、为什么”；作者事后逐个提交 review。
~~遇到计划里没写清、需要作者拍板的取舍时停下来问，不要自己定。~~
（2026-10-06 作者开工时改为：**不停下来等**，把取舍记进下方“执行中的取舍（待作者确认）”，选最稳妥的方案继续。）

**开工前按顺序做：**

1. 读 AGENTS.md（代码风格、测试、运行环境三节是硬约束：中文注释讲“为什么”、不写防御性代码、
   一个测试对应一个会出事故的场景、只用 `conda run -n margin`）。
2. 读这些已定下来的设计，不要重新讨论：
   - PLAN §5.7：RabbitMQ 配置、`requeue_lost` 三条规则、补发上限、查询 SQL、6 个测试、切换步骤、LISTEN / NOTIFY 小节；
   - PLAN §5.4：M2 的“有新事件”通知**已定用 PG LISTEN / NOTIFY**（D20），逐字片段走 Redis pub/sub；
   - DECISIONS D19（为什么换 RabbitMQ、被放弃的方案）、D20。
3. 背景（可选）：`review/m1_review.md` 第四、五章是作者 review 时的问答，讲清了租约、`_fence`、Celery 进程结构、
   RabbitMQ 预取与确认、水位线对账的来龙去脉。
4. 确认基线：`conda run -n margin python -c "import sys; print(sys.prefix)"` 指向 margin 环境；
   起 PG / Redis 后跑 `pytest` 和 `pytest -m integration`，M1 的 75 个测试应全部通过，再开始改代码。

**实现时要留意的点（review 时讨论过，计划里只写了结论）：**

- RabbitMQ 容器要**固定主机名**（数据目录按节点名区分）；`consumer_timeout` 用配置文件挂进 `/etc/rabbitmq/conf.d/`，
  设 1 小时，必须大于 Celery 硬时限 25 分钟。
- 保留 `task_acks_late=True`、`worker_prefetch_multiplier=1`；删 Redis 专用的 `visibility_timeout`。
- 发送确认打开后，“拒收 / 未确认”抛什么异常要**实际验证**后再写进 dispatcher 的 except（现在只捕获
  `kombu.exceptions.OperationalError`）。
- `requeue_lost` 的队列状态（就绪数、消费者数）**作为参数传入**，集成测试不依赖真实 RabbitMQ；
  读队列状态用被动声明 `queue_declare(passive=True)`，连不上时跳过“队列为空”规则。
- 补发**复用同一行 outbox**（改回 pending），否则会违反“同一 attempt 最多一条 pending”的部分唯一索引；
  `redeliveries` 是新列，不要复用 `tries`（它用来算退避）。
- 宽限期 2 分钟（子进程第一次执行要加载语料）；所有状态更新都带状态条件，和 worker 的 `claim` 并发时安全。
- LISTEN 用一条**专用的自动提交连接**，不从连接池借；`time.sleep(1)` 换成 psycopg 3 的 `notifies(timeout=...)`，
  兜底扫描最长 10 秒；专用连接断开就退出进程，交给 compose 重启。
- 手动故障演练的结果写进 STATUS 的 M1.5 记录（和 M1 记录的写法一样）。

**提交方式（和 M1 一样，作者要求）**：

- 小步提交，**一个提交只做一件事、作者一次能 review 完**（M1 是 M1-1 … M1-7，每个几十到两三百行）。
  步骤表里偏大的步骤要再拆，例如第 2 步可拆成“compose + 配置”和“worker / dispatcher 改用 RabbitMQ”两个提交；
  第 3 步可拆成“对账查询 + 判定”和“补发上限 + 判失败”。
- 提交信息格式：`M1.5-1：迁移 0002——outbox 加补发次数 + 部分索引`（M2 用 `M2-1：…`），正文写“做了什么、为什么”，
  结尾带 Co-Authored-By 行。代码和它的测试放在同一个提交里。
- 每个提交前：`pytest`、`pytest -m integration`、`ruff check src tests scripts` 都通过。
- **作者不在旁边盯着，说明一律写进代码导读，不只在对话里说。** 里程碑开工时就在 `review/02_代码导读/` 建好
  `M1.5_队列与对账.md`（M2 为 `M2_过程可视化.md`），并加进该目录 README 的表格。**每个提交都在导读里追加一节**，
  和代码放在同一个提交里：
  - 标题带提交编号，如“M1.5-3：对账查询 + 判定”；
  - 改了什么：涉及的文件和函数（写成可点击的相对链接，带行号）；
  - 为什么这样做：引用 PLAN / DECISIONS 的对应条目，有实现时的取舍就写清楚；
  - 用一个具体例子讲清楚（例如“attempt 200 投递后队列被清空，巡检第几秒怎么判定、改了哪几行数据”）；
  - 怎么验证的：新增 / 改动的测试名及其保护的场景，手动验证的命令和看到的结果；
  - review 时建议重点看的地方。
  概念解释用中文说法（第一次出现可括注英文），读者是后端初学者。
- 里程碑结束时单独一个收尾提交（参照 M1 的 b4ed4f3）：把导读补全成和 `M1_后端服务.md` 同样的总览结构
  （对应提交范围、新增 / 改动文件、阅读顺序、数据流、测试地图），放在逐步记录之前；更新 STATUS（写“M1.5 记录”：
  提交范围、故障演练结果、和方案的差异）、PLAN 里程碑打勾、DECISIONS 补实现时的取舍、README、
  `review/01_运行链路.md`。M2 结束时同样处理，前后端联动 10 个场景的验证结果也写进 M2 导读。

**和作者沟通**：作者是后端初学者、在准备面试，会事后逐个提交 review，过程中不一定在场。对话里只需简短说明进度，
详细说明以代码导读为准。

## 已完成

- 仓库骨架：conda 环境 `margin`（`environment.yml`）+ pyproject、ruff、pytest、`.env.example`、
  工作约定 AGENTS.md。
- RC6-C Harness 轻量移植（`src/margin/harness/`）：提示词与工具 schema 原样保留；十个工具；
  笔记压缩 + 思考回灌主循环。保留和简化的内容见 [docs/MIGRATION.md](docs/MIGRATION.md)。
- 检索（`src/margin/retrieval/`）：BM25（scipy 稀疏矩阵）+ 实体别名 + 可选向量，RRF 融合。
  在真实语料上验证：17,596 个 block，首次建索引约 100 秒，缓存 41MB，之后加载 1.3 秒。
- OpenAI 兼容模型客户端（`src/margin/llm/`），统一 reasoning_content / reasoning 字段。
- 92 个后端测试：51 个单元测试（`conda run -n margin pytest`）+ 41 个接口 / 集成测试（`pytest -m integration`，连真实 PG / Redis），全部通过，ruff 无报错；前端 14 个 vitest（`cd web/app && npm test`）。
- M1.5：任务队列换成 RabbitMQ（发送确认、持久化）；巡检对账补发丢失的消息；dispatcher 由 LISTEN / NOTIFY 唤醒。
- M2：两层事件（PG 通知驱动的持久事件 + Redis 实时思考片段）；React 前端 `web/app/`（简洁风 + 衬线 + 石墨），前后端联动 10 个场景浏览器实测通过。
- 前端设计稿第三版 `web/design/`，只在本地打开 `preview.html`：
  - 三种风格：简洁「批注版式」（`clean.css`，含衬线开关）、手绘（`sketch.css`，作者已认可，外观不再改）、
    瑞士 + 扁平矢量（`swiss.css`）；公共结构与动效在 `base.css`。
  - 五套配色（墨青 / 石墨 / 藏青 / 松烟 / 宣纸）× 深浅色；瑞士风用固定的黑白红。
  - 小动效：提交时画圈打勾、胶囊输入框聚焦展开、发送↔停止、标题文字交替、节点弹出与呼吸、
    数字滚动、荧光笔扫过、历史悬停滑块、复制 ✓、换色渐变。
  - 来源区：整栏收起 / 展开、原文 3 行 → 完整段落展开、点引用跳转并闪烁；简洁风里来源是页边旁注，
    与引用对齐，悬停时画连线。
  - 右下角“设计评审”面板切换，也可用地址参数，如 `preview.html?style=clean&font=serif&palette=paper`。
- 运行环境约定写入 AGENTS.md（D10）。

## M0.5 记录（真实运行）

0. 前端方案已选定（D14）：简洁风 + 衬线 + 石墨 + 浅色为主，手绘风为候选。
1. ~~接通真实模型~~（2026-10-04 完成）：学校网关，主用 `DeepSeek`（v4.1-flash），备用 `GLM`（glm5.3-flash），
   用 `--model` 切换。首次真实运行：“广晟控股 2023 年营业收入”9 轮完成，逐字引用合并利润表，答 1275.99（亿元）。
   网关上三个对话模型都支持工具调用，也接受历史里的 `reasoning_content`。
2. ~~三类题各跑通~~（2026-10-04 完成）：E80 里挑 7 道开放检索题（数值 2、百分比 2、判断 / 单选 / 多选各 1），
   两个模型各跑一遍，向量检索关闭：

   | 模型 | 答对 | 平均调用次数 | 平均耗时 | 平均输入 token |
   |---|---|---|---|---|
   | DeepSeek | 6/7 | 13 | 80s（最慢一题 336s，单轮最长 90s） | 35 万 |
   | GLM | 5/7 | 20 | 60s | 32 万 |

   - 两个模型都错 `res_b_005`（标准 22.19%）：DeepSeek 交 22.00%，GLM 交 22.20%，都是中间结果提前取整。
   - GLM 在多选题 `fin_b_005` 上用完 30 轮（12 次 read_section）没有提交；DeepSeek 21 轮答对。
   - 另见：DeepSeek 曾把“营业总收入”当成“营业收入”答（1277.31 vs 1275.99），同一题另两次答对。
   - 结论：DeepSeek 主用不变；M1 的重试策略要区分“网关失败”（换模型重跑）和“答错”（不自动重跑）。
3. ~~LLM 客户端流式输出~~（完成）：`chat(..., on_delta=回调)` 走 SSE，思考逐字回调，工具参数分片拼接，
   最后一块取 usage；`run_episode(on_delta=...)` 给每个片段带上轮次。脚本加 `--stream` 可直接看效果。
4. ~~向量检索~~（2026-10-04 完成，D15 / D16）：对齐 RC6-C 当前索引 `rc6-local-qwen3emb06b-q8_0-v2`
   （从实验室服务器拷回，7 个文件 sha256 一致），向量导入 PG（pgvector，精确检索）；查询向量由本机 CPU 上的
   llama.cpp（compose 的 embedding 服务，端口 18082）计算。本机重算与 v2 余弦最低 0.99947；
   E80 80 题 search_docs 结果与 Chroma 完全相同 76/80。查询格式修正为“指令 + 空格 + 查询”。
5. ~~E80 全量~~（2026-10-04 完成）：margin Harness + 三路检索，官方评分器（与服务器 sha256 一致，
   重判历史 560/560 复现）。结果在 `.cache/e80_20261004/`（不进仓库）：

   | 模型 | 答对 | 有效提交 | 平均调用 | 平均耗时 | 每题输入 token |
   |---|---|---|---|---|---|
   | DeepSeek | **66/80** | 79 | 15.1 | 104s | 36 万 |
   | GLM | 57/80 | 65（15 题用完 30 轮） | 21.0 | 132s | 35 万 |
   | 参照：RC6-C 自训 4B 最好 / 27B（服务器，完整 RC6-C） | 48 / 59 | | | | |

   - DeepSeek 错的 14 题里 11 题是多选少选 / 多选；GLM 主要失败是轮数用完不提交。
   - 两个模型都对 54 题，至少一个对 69 题。160 次运行网关没有出错。
6. ~~架构对齐与租约方案修订~~（2026-10-05 完成，D17）：见 `review/03_架构对齐与租约方案.md`，已写入 PLAN §5.2。

## M1 记录（2026-10-05 完成，提交 fbb6879 … 2651c80，取舍见 D18）

做了什么（按提交顺序）：

1. 配置模块 `settings.py`（pydantic-settings）+ 共用组装 `assembly.py`；删死代码 `answers_match`。
2. SQLAlchemy 模型 + Alembic 首个迁移（users / runs / attempts / steps / events / outbox / block_vectors）；
   开发库已迁移，v2 向量重新导入。
3. Harness 加 `on_step`（每步完成即回调，回调抛异常直接中断本题）。
4. `runs.py`（建任务 + Outbox + 幂等、重新生成、查询、事件）、`lease.py`（领取、带 epoch 提交、续租、巡检）。
5. FastAPI：`POST /runs`、`GET /runs/{id}`、`POST /runs/{id}/regenerate`、`GET /runs/{id}/events`（SSE）。
6. Celery worker + dispatcher（outbox 投递、每 15 秒巡检）；模型调用超时 10s / 60s / 180s。
7. Dockerfile + compose（加 redis、migrate、api、worker、dispatcher，共 7 个服务）。

端到端实测（真实 DeepSeek，compose 全部服务）：

- 同一个幂等键提交两次都返回 run 1；worker 13 步完成，SSE 按 seq 推出 16 个事件后关闭；
  `GET /runs/1` 能看到每一步。答案 1277.31（“营业总收入”，标准 1275.99），是 M0.5 记录过的同一口径问题。
- 重新生成返回 202，执行中再点返回 409。
- 故障演练：第 2 次执行进行到第 6 步时 `docker compose kill worker`，86 秒后巡检判为 `lease_expired`，
  6 步保留；重启 worker 后服务正常。
- 和方案的差异：块间超时是 60s 而不是 30s（httpx 只有一个读超时）；checkpoint 推迟到 M4、llm_calls 推迟到 M3。

## M1 review（2026-10-06 结束）

作者按技术点逐个 review M1，问答整理在 [review/m1_review.md](review/m1_review.md)：
迁移、assembly 与检索数据存放、设计模式面试问答、dispatcher / worker / lease 与消息队列、
程序入口与运行流程 + 代码细读（已完成五章）。
review 中发现的问题和决定：

- **缺口**：消息在消息中转站里丢失后，attempt 永远停在 pending，重新生成也返回 409（M1 的巡检只处理 running）。
- **决定（D19）**：任务队列换成 RabbitMQ；巡检增加对账补发（水位线为主）。完整方案见 PLAN §5.7。
- **决定**：M6 加监控告警（`/metrics` + Prometheus + Grafana），规则草稿在 review 第四章第 10 节。

## M1.5 记录（2026-10-06 完成，提交 e77012a … aa414b3 + 收尾，取舍见 D21）

逐个提交的说明、总览、测试地图在 [review/02_代码导读/M1.5_队列与对账.md](review/02_代码导读/M1.5_队列与对账.md)。

做了什么（按提交顺序）：

1. M1.5-1 迁移 0002：outbox 加 `redeliveries` + 部分索引 `ix_outbox_sent_at`；开发库 0001→0002 数据不丢。
2. M1.5-2 compose 加 rabbitmq（`rabbitmq:4-management-alpine`、数据卷、`hostname: rabbitmq`、`consumer_timeout` 1 小时）。
3. M1.5-3 worker / dispatcher 改用 RabbitMQ；dispatcher 用自己的 publisher 打开发送确认 + 超时；`clock_timestamp()`。
4. M1.5-4 对账 `requeue_lost`：三条规则 + 补发。
5. M1.5-5 补发上限 3 次后判 `delivery_lost`；被动声明读队列状态；接进巡检。
6. M1.5-6 dispatcher 用 LISTEN / NOTIFY 唤醒。

结束条件核对：

1. 自动化：84 个测试（单元 51 + 集成 33，M1 的 75 个 + 新增 9 个）全部通过，ruff 无报错，`alembic check` 无差异。✅
2. 故障演练（2026-10-06，compose 8 个服务 + 真实 DeepSeek）：✅
   - **重启 RabbitMQ**：停 worker 后提交，队列就绪 1 条；`docker compose restart rabbitmq` 后仍是 1 条；启动 worker 后完成。
     另外观察到：RabbitMQ 重启后 dispatcher 第一次投递失败（旧连接已断），2 秒退避后成功（取舍 2 预计的代价）。
   - **清空队列**：停 worker、提交、管理接口清空队列；2 分 30 秒内消费者为 0 **不补发**；启动 worker 13 秒后巡检补发
     （规则 ②“队列为空”），4 秒后被领取，10 秒完成，`redeliveries=1`。
   - **执行中 kill worker**：第 3 步后 kill，RabbitMQ 立刻把未确认的消息放回队列；97 秒后判 `lease_expired`，3 步保留；
     重启 worker 后收到那条消息，`claim` 拒绝并跳过。
   - **LISTEN / NOTIFY**：提交到投递 8～43 毫秒；空闲 40 秒 dispatcher 只查了 6 次库（间隔 5～11 秒）。
3. Redis 不再承担任务队列：worker / dispatcher 的 broker 和 `depends_on` 都是 rabbitmq；`MARGIN_REDIS_URL` 只留给 M2 推流。✅
4. 文档：D19 与实现一致（差异记在 D21）；README、运行链路图更新为 8 个服务。作者 review 待进行。

和方案的差异：见下方“执行中的取舍”1～5，以及 worker 临时队列改为独占（RabbitMQ 4 的要求）。

原计划的步骤表（全部完成）：

| 步骤 | 内容 | 这一步的完成标准 |
|---|---|---|
| 0. review 收尾 ✅ | 本轮文档（m1_review 五章、PLAN §5.4 / §5.7、D19、STATUS）已提交 | 作者已确认 review 结束 |
| 1. 迁移 0002 ✅ | outbox 加 `redeliveries`（默认 0）+ 部分索引 `outbox(sent_at) WHERE status='sent'`；models.py 同步 | 空库 `upgrade head` 成功；已有 M1 数据的库从 0001 升到 0002 数据不丢；`alembic check` 无差异 |
| 2. 接入 RabbitMQ ✅ | compose 加 `rabbitmq`（数据卷、固定主机名、健康检查、`consumer_timeout` 1 小时）；`MARGIN_BROKER_URL`；worker 改 broker、删 `visibility_timeout`、开发送确认；`celery[redis]` → `celery`；dispatcher 捕获“拒收 / 未确认” | `docker compose up` 全部健康；提交一道题能跑完；管理界面能看到队列和 4 个预取；改造后的“中转站不可用时退避”测试通过 |
| 3. 对账 `requeue_lost` ✅ | 水位线 / 队列为空 / 30 分钟兜底三条规则；补发上限 3 次后判 `delivery_lost`；消费者为 0 不补发 | PLAN §5.7 的 6 个集成测试通过 |
| 4. LISTEN / NOTIFY ✅ | `_enqueue_attempt`、`requeue_lost` 里 NOTIFY；dispatcher 专用连接 LISTEN，兜底扫描最长 10 秒 | “提交后 1 秒内投递（不靠兜底扫描）”“事务回滚不投递”两个测试通过 |
| 5. 验收与文档 ✅ | 手动故障演练；README、`review/01_运行链路.md`、`review/02_代码导读` 加 M1.5；STATUS 记录 | 见下方结束条件 |

**M1.5 结束条件（全部满足才算完成）：**

1. 自动化：全部单元 / 集成测试通过（M1 的 75 个 + 新增约 9 个），`ruff check` 无报错，`alembic check` 无差异。
2. 故障演练（手动，结果记进 STATUS）：
   - 提交后、worker 领取前**重启 RabbitMQ** → 消息不丢，任务正常完成（验证持久化）；
   - 停掉 worker、提交任务、在管理界面**清空队列**、再启动 worker → 巡检补发，任务完成（验证对账）；
   - 执行中 `docker compose kill worker` → 约 90 秒后判 `lease_expired`，已完成的步骤保留（M1 行为不退化）；
   - 新任务从提交到被投递 **< 1 秒**，dispatcher 空闲时不再每秒查库（验证 LISTEN / NOTIFY）。
3. Redis 不再承担任务队列（代码和 compose 里 worker / dispatcher 不再依赖 redis）。
4. 文档：D19 与实现一致；README 的启动说明、运行链路图更新为 8 个服务；作者 review 完全部提交。

## M2 记录（2026-10-06 完成，提交 521427e … dfcd93a + 收尾，取舍见 D22）

逐个提交的说明、总览、测试地图、10 个场景的详细现象在 [review/02_代码导读/M2_过程可视化.md](review/02_代码导读/M2_过程可视化.md)。

做了什么（按提交顺序）：

1. M2-1 持久事件改为 PG 通知驱动：`add_event` 同事务 `pg_notify('run_events', run_id)`；API 每进程一条 LISTEN（`event_hub.py`），
   SSE 等通知、最多 10 秒兜底查一次。
2. M2-2 实时片段：worker 把思考逐字 publish 到 Redis `run:{id}:live`（带 attempt_id + epoch），SSE 转发为 `event: delta`（不带 id）。
   真实 DeepSeek 一道题约 500～600 个片段。
3. M2-3 `?after=` 参数（新开 EventSource 时代替 Last-Event-ID）、`GET /runs` 历史列表。
4. M2-4 前端骨架：Vite + React + TS，/api 开发代理，设计稿样式，接口封装、路由、侧栏、输入框。
5. M2-5 时间线：事件合并纯函数（按 seq 去重、按 epoch 丢片段、step 替换片段）、SSE 订阅 hook、思考与工具卡片。
6. M2-6 回答与引用：结论行、依据列表、页边来源旁注对齐、点 [n] 跳到原文、执行中跟随到底部。
7. M2-7 重新生成（409 提示）、失败说明（网关错误 / 异常 / lease_expired / delivery_lost）。
8. M2-8 联调发现的“半死”SSE 连接：心跳改为具名事件 `event: ping`，前端 25 秒无数据重建；uvicorn 优雅退出 5 秒；`#root` 高度。

前后端联动 10 个场景（2026-10-06，compose 8 个服务 + `npm run dev` + 真实 DeepSeek，Chrome 实测）：

| # | 场景 | 结果 | 要点 |
|---|---|---|---|
| 1 | 提交一道题 | ✅ | 202，0.1 秒内建立 SSE（`text/event-stream`，经 Vite 代理逐条到达） |
| 2 | 执行过程 | ✅ | 思考逐字出现；工具卡片、思考全文与库一致，无残留片段；点 [n] 旁注展开闪烁；执行中跟随到底部 |
| 3 | 执行中刷新 | ✅ | 已完成的步骤 1.5 秒内恢复，新连接事件 1–14 连续，结束时与库一致 |
| 4 | API 断开后续传 | ✅（修复后） | 首次失败：Vite 代理在 API 进程死掉后不关浏览器侧连接，页面永远卡住 → M2-8 修复；之后 25 秒检测到并用 `?after=` 续上，事件连续无重复 |
| 5 | 连点两次提交 | ✅ | 双击只发 1 次；“请求到达后端但响应丢失”后重试复用幂等键，得到同一个 run |
| 6 | 重新生成 | ✅ | 新执行出现、旧执行保留；执行中再点 409 提示 |
| 7 | 失败路径 | ✅ | 网关 404 → “模型网关返回错误（HTTP 404）”；kill worker → 约 93 秒后“执行中断”，已完成的 2 步保留；之后无重连循环 |
| 8 | 旧 epoch 的片段 | ✅ | 人工发出的旧执行、旧 epoch 片段浏览器收到但不显示，当前执行的对照片段显示 |
| 9 | 非法输入 / 别人的 run | ✅ | 422“问题最多 2000 字”；不存在的 run、别的用户的 run、非法地址都显示 404 页 |
| 10 | 控制台和网络 | ✅ | 无页面异常；只有故意触发的 4xx 和模拟断网；没有定时轮询 |

和方案的差异：心跳从 SSE 注释改为具名事件（M2-8）；旁注对齐没有做“提前算终点”和连线；其余见下方“执行中的取舍”9～14。
联调脚本（playwright-core）在临时目录，没有进仓库。开发库里为场景 9 插入了一个用户 `m2-other-user` 和它的 run 22。
PLAN 里 M2 的验收写的是“演示视频”：这次只留了截图和请求记录，没有录视频，需要作者自己录（`npm run dev` 后提交一道题即可）。

## M2 补完（A）记录（2026-10-06 完成，提交 5536b99 … ac2be4e，导读见 M2 导读“M2 补完（A）”）

| 提交 | 内容 | 浏览器实测 |
|---|---|---|
| M2-10 | 来源栏执行中吸顶列表、结束后只对齐回答 [n]（FLIP 切换）；提前算终点；结束时滚到结论；原文按实际高度展开；`.app` 行高 max-content（修 sticky 失效） | run 32、35–39：执行中卡片屏幕位置不动；展开过程区时卡片与 [n] 每帧差 5–6 px；结束时没有空白帧 |
| M2-11 | 卡片入场动画；执行中新卡片出现时从引用步骤画线、1.5 秒后收回 | run 40、41：线的起点在引用那一行、终点在卡片左上 |
| M2-12 | “检索命中的其他文档”分组（读过 n 块 / 检索排名）；没有引用时右栏不再是空的 | run 31（0 引用）、run 41 |
| M2-13 | 步骤长出来、思考逐段淡入、标题切换；跟随到底部改为盯正文高度 | run 42：677 次采样离底部最远 45 px |
| M2-14 | 结论数字滚动；复制按钮 | run 42 滚到 1206.37；run 41 剪贴板内容核对 |
| M2-15 | 历史列表悬停滑块；来源栏整栏收起 | run 41：收起 0.4 秒、正文回到居中、展开后卡片仍对齐 |

逐帧发现的 6 个问题（P1–P6）都已处理。没做的：回答 [n] 与卡片之间的悬停连线（等 M2.5 有了正文里的 [n] 再做）；
发送 / 停止按钮的切换（停止要等 M4 的取消）。

## M2.5 记录（进行中）

- **M2.5-0 实验**（2026-10-06，作者确认）：E1–E5 的结果和据此做的调整见 PLAN §5.8“实验结果”、D23。
  脚本在 `scripts/experiments/m25/`，结果在 `.cache/m25/`（不进仓库）。学校网关 4 并发会被 429 限流，实验改为 2 并发 + 重试。
- **M2.5-1 引用编号移到后端**：`citations.py`，随 step 事件下发 `citation_no`；迁移 0003 给旧事件补编号。
- **M2.5-2 理解题目**：迁移 0004（`runs.title`、`answer_label`，`answer_format` 可空）；worker 第一次执行时
  在另一个线程里理解题目、写 `run_understood` 事件，finalize 只在没给格式时等它；重新生成不再调用。
  接口的 `answer_format` 改为可选（评测回放仍然传）。实测 run 44：8 秒出标题，判为 num，181 秒完成。
- **M2.5-3 引用提醒**：迁移 0005（`runs.require_citation`，默认 true，评测回放传 false）；
  finalize 没有引用时提醒一次（`citation_required`），第二次照收并在结果里标 `uncited`。
- **M2.5-4 撰写回答**：`compose.py`，Harness 结束后用证据包（E2 输入 a）写带 [n] 的回答，`kind="answer"` 流式推送；
  进库前删不存在的 [n]、标出未核实数字，存 `final.written`，事件 `answer_written`；失败时 `written = {error}`，这道题照常完成。
  实测 run 45：撰写 25 秒，[1] 与来源卡片一致，未核实数字 0。
- **M2.5-5 前端**：输入框只剩文本框；左栏、标题栏显示标题；过程区首尾加“理解题目”“撰写回答”；
  回答区按设计稿（结论行、带 [n] 的正文、口径说明、uncited / 撰写失败 / 未核实数字三种小字），悬停 [n] 画连线。
  浏览器验证 run 45/46/47/41。遗留：执行中刷新时回放触发一次“新卡片连线”（M2-11 的问题）、账目行没有耗时。
- **M2.5-5 修订**（作者试用反馈）：结束后右栏从过程区上沿排起（D24）；旁注去掉原文里的 `<sub>` 标签和图片链接；
  修复思考流式结束、step 到达时整段思考重播出现动画（条目不在同一个数组里，React 删掉重建）。
- **M2.5-5 修订二**：执行中右栏也从过程区上沿排起、到标题下方再吸住；“理解题目”执行一开始就占位转圈；
  SSE 补发完历史后发 `caught_up`，刷新时回放出来的卡片不画连线；账目行加耗时。上面两处遗留都已解决。
- **M2.5-5 修订三（D25）**：理解题目猜的格式只软校验（对不上按文本收下），和给定的格式分两列存（迁移 0006）；
  理解题目提示词区分“时间要求”（文本）和“哪一天”（日期）。起因 run 66：猜错格式逼模型编出两个日期。
- **M2.5-5 修订四**：交卷后按“过程区收起 → 结论淡入、数字滚动 → 正文逐字”出场（大字不再比正文晚一拍），百分比也滚动；
  理解题目两行先占位、原地切换，高度不变；修复连线 SVG 撑住页面高度、过程区收起后满屏空白。
- **实验 E6（理解题目的格式判断）**：214 道题，一致率现行提示词 94%、候选（`scripts/experiments/m25/prompts/understand_v2.txt`）98%，
  num 召回 85% → 98%。**待作者确认后换到线上**（`src/margin/prompts/understand.txt`）。
- **未决：模型交的数值常带单位**（run 83 交“1,332.20亿元”、run 85 交“541.61亿元”），按 num 收不下、按文本收下，没有大字。
  方案待作者确认（对话里给出）。
- **未决：`date_calc` 的 same_day 按月 / 年少算一整个月 / 年**（run 66 第 6 步：2026-01-20 起 6 个月给出 6 月 20 日）。
  训练仓库 finetune 的 `calculate_date` 是同一段逻辑，改动会和训练环境不一致，待作者决定。
- RL 数据收集方案已在对话里给出草稿，作者之后再看，暂不写进 PLAN。

## 下一步：M2.5 回答形态（B）→ M3

2026-10-06 作者试用网页后提出两类问题，方案已在对话里批准（PLAN §7 两行、§5.8、D23）：

1. **M2 补完（A）**：已完成，见上一节。
2. **M2.5 回答形态（B）**：实验已完成并确认，按 §5.8 的提交拆分写正式代码，下一个是 M2.5-6（收尾：导读总览、STATUS / DECISIONS 整理）。
   引用规则是“提醒一次、第二次照收、页面标出无引用”。自训模型等实验室服务器空出来后用同样的脚本复测。
3. **RL 数据收集方案**：草稿已在对话里汇报，作者之后再看。
4. 然后是 M3 多用户与治理（下面是原来的说明）。

### M3 多用户与治理

先请作者 review M1.5、M2 的提交（导读 `review/02_代码导读/M1.5_队列与对账.md`、`M2_过程可视化.md`），
确认下方“执行中的取舍”，确认后移进 DECISIONS（D21、D22 定稿）。

M3 范围（PLAN §7）：登录、owner 隔离、配额、LLM 信号量、分层重试、开发者视图、证据缓存。
验收：A 看不到 B 的题；10 个并发任务时模型并发不超过上限。开工前先和作者细化方案（PLAN 里 M3 只有一行）。
前端相关：`api.py` 的 `DEV_USER_ID` 换成从登录 cookie 解析；侧栏底部的账号与配额（设计稿有）在 M3 接上。

## 执行中的取舍（待作者确认）

M1.5 / M2 执行时遇到、计划里没写清的取舍。每条写“问题 → 选了什么 → 为什么 → 不同意时怎么改”，
作者 review 时逐条确认，确认后移进 DECISIONS。

1. **发送确认开在 dispatcher 自己的 Celery 实例上，而不是 worker.py 的 celery_app**（M1.5-3）：
   只有 dispatcher 发任务消息；同时要给连接加 socket 读写超时（实测不加时，RabbitMQ 告警会让 dispatcher
   永远卡在关闭通道），这个超时不想加到 worker 的连接上。代价：两处 Celery 实例要保持任务名、队列名一致（都用默认）。
   不同意的话：把 confirm_publish 和超时挪进 worker.py 的 celery_app，dispatcher 改回导入它。
2. **关闭 Celery 自带的发送重试**（`task_publish_retry=False`，M1.5-3）：dispatcher 已有退避，叠加后一次失败会卡 20 秒。
   代价：RabbitMQ 重启后第一条消息可能失败一次、等 2 秒退避后再发。
3. **dispatcher 的时间改用 `clock_timestamp()`**（M1.5-3）：修复 M1 遗留——`now()` 是事务开始时间，
   发送耗时超过退避时间时退避失效；同一批的 `sent_at` 相同会让水位线分不出先后。
4. **水位线的“已被领取”用 `attempts.started_at IS NOT NULL`，不用 PLAN 写的 `status <> 'pending'`**（M1.5-4）：
   判为 `delivery_lost` 的 attempt 状态是 failed 但从没被领取，按状态算会抬高水位线、让前面正常排队的任务被误判丢失。
   有测试 `test_attempt_failed_as_undelivered_does_not_raise_the_watermark` 保护。
5. **读队列状态时队列不存在（`NotFound`）当作“没有消费者”**（M1.5-5）：只可能是 RabbitMQ 数据没了且 worker 不在线；
   不处理的话 dispatcher 会每 15 秒崩溃重启一次。
6. **API 的事件通知用后台线程 + 同步 psycopg 做 LISTEN**（M2-1）：psycopg 异步连接不支持 Windows 的 Proactor 事件循环，
   开发和测试都在 Windows 上。生产在 Linux 容器里，以后想换成异步版只改 `event_hub.py`。
7. **实时片段每个 SSE 连接各自订阅 Redis**（M2-2），不像 PG 那样每进程一条再分发：Redis 连接便宜；
   人多时（上千个同时观看）再改成每进程一个订阅。
8. **Redis 不可用时只丢实时片段**（M2-2）：worker 第一次 publish 失败后这次执行不再发，API 订阅失败只记日志。
9. **前端页面状态全部来自事件流**（M2-5）：打开题目页用 `?after=0` 订阅，服务端补发全部历史事件；`GET /runs/{id}` 只取题目和判断 404。
   代价：一道很长的题打开时要补发几十个事件（每个带完整工具结果），比直接取步骤列表多传一些数据；换来刷新 / 历史 / 执行中同一条路径。
10. **“重新生成”按钮执行中也能点，由后端 409 拒绝**（M2-7）：前端状态可能是旧的，以后端为准。不同意的话：执行中禁用按钮，409 提示保留。
11. **旁注对齐是设计稿算法的简化版**（M2-6）：每帧按当前位置重算，不“提前算动画终点”；不画 [n]↔旁注的连线。
    来源上下文只取自模型读过的步骤，“在原文中打开”等 M5。
12. **SSE 心跳改为具名事件 + 前端 25 秒超时重建**（M2-8，后端改动）：联调发现 Vite 代理在 API 进程死掉后不关闭浏览器侧连接；
    心跳 + 客户端超时是长连接的通用做法，换成 Nginx 也保留。
13. **uvicorn `--timeout-graceful-shutdown 5`**（M2-8）：SSE 连接不会自己结束，不设的话每次停 API 都要等 docker 10 秒强杀。
14. **历史列表不轮询**（M2-4）：只在打开页面、提交、正在看的题结束时重新取；别的题的状态点不实时。

## 未决问题

1. ~~DeepSeek 是否拒绝历史里的 `reasoning_content`~~：学校网关上不拒绝（2026-10-04 实测）。
   若以后接 DeepSeek 官方 API 再验证一次。
2. ~~向量检索未在真实环境验证~~：已验证（见上）。
4. ~~embedding 服务放哪里~~：已放进 compose（D16），Windows 版 llama.cpp 已删除。
3. 文档身份 / 表格上下文两类结果增强未移植，先观察自训模型在缺少它们时的表现。
5. 检索拆成独立服务：现在每个 worker 子进程各加载一份语料 + BM25（约 1.2GB / 进程），
   内存随并发线性增长。作者倾向于后续拆成独立检索服务，具体放在哪个里程碑待定。
6. M1 的卡死检测有盲区：主线程卡住但续租线程还活着时，租约会一直被续上，只能等 25 分钟硬时限。
   M4 的监督者（续租线程同时检查主线程有没有进展）解决。
7. 浏览器自带的 Last-Event-ID 自动重连在开发环境（经 Vite 代理）一次也没触发过，续传都走前端的 `?after=` 重建；
   这条路径只有后端测试覆盖。M6 上 Nginx 后在浏览器里再验证一次。
8. 是否把前后端联动的 10 个场景做成仓库里的端到端测试（Playwright，在 `web/app` 下）：M2 只做了一次性的浏览器实测，
   脚本没进仓库。作者决定。

## 本地环境备忘

- 语料路径在 `.env`，默认指向 `D:/competition/finetune/data/`（只读使用）。
- BM25 缓存在 `.cache/bm25/`（不进仓库），语料变化时删除重建。
- WSL Docker 可用（2026-10-03 验证：`flora` 已在 docker 组；Windows 经 localhost 能连到 WSL 容器端口）。
  注意：WSL 空闲时会自动关机，容器随之停止。跑服务时要保持一个 WSL 终端开着，
  或在 `%UserProfile%\.wslconfig` 里调大 `vmIdleTimeout`。
- 前端开发：`cd web/app && npm install && npm run dev`，打开 http://localhost:5173（/api 转发到 127.0.0.1:8000 的 api 容器）。
  改了后端代码要 `docker compose up -d --build api worker dispatcher`，否则容器里还是旧代码（M2 联调时踩过）。
- Windows 上的脚本经 localhost 连 WSL 里的 PG，发送大于约 20KB 的请求会多约 45ms（转发层，D18）；容器之间没有这个问题。
