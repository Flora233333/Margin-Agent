# 当前状态

更新：2026-10-06

## 所在阶段：M1 完成并 review 结束；下一个会话连续执行 M1.5（队列换 RabbitMQ + 对账 + 通知唤醒）→ M2

计划全文见 [docs/PLAN.md](docs/PLAN.md)。

## 新会话交接（2026-10-06 写，给执行 M1.5 → M2 的会话）

**任务**：先做 M1.5（下方“下一步”表格的第 1～5 步），满足 M1.5 结束条件后，接着做 M2（“再下一步”）。
两个里程碑连续推进：每一步一个小提交，提交信息说清“做了什么、为什么”；作者事后逐个提交 review。
遇到计划里没写清、需要作者拍板的取舍时停下来问，不要自己定。

**开工前按顺序做：**

1. 读 AGENTS.md（代码风格、测试、运行环境三节是硬约束：中文注释讲“为什么”、不写防御性代码、
   一个测试对应一个会出事故的场景、只用 `conda run -n margin`）。
2. 读这些已定下来的设计，不要重新讨论：
   - PLAN §5.7：RabbitMQ 配置、`requeue_lost` 三条规则、补发上限、查询 SQL、6 个测试、切换步骤、LISTEN / NOTIFY 小节；
   - PLAN §5.4：M2 的“有新事件”通知用 PG NOTIFY 还是 Redis，**M2 开工时比较后写进 DECISIONS**；
   - DECISIONS D19（为什么换 RabbitMQ、被放弃的方案）。
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
- 里程碑结束时单独一个收尾提交（参照 M1 的 b4ed4f3）：更新 STATUS（写“M1.5 记录”：提交范围、故障演练结果、
  和方案的差异）、PLAN 里程碑打勾、DECISIONS 补实现时的取舍、README；并在 `review/02_代码导读/` 新增
  `M1.5_队列与对账.md`（格式照 `M1_后端服务.md`：对应提交、新增 / 改动文件、阅读顺序、数据流、测试地图、review 重点），
  同时更新该目录的 README 表格和 `review/01_运行链路.md`。M2 结束时同样处理。

**和作者沟通**：作者是后端初学者、在准备面试，会逐行 review。解释概念时用中文说法（第一次出现可括注英文），
多举例子；每一步完成后简短说明改了什么、怎么验证的。

## 已完成

- 仓库骨架：conda 环境 `margin`（`environment.yml`）+ pyproject、ruff、pytest、`.env.example`、
  工作约定 AGENTS.md。
- RC6-C Harness 轻量移植（`src/margin/harness/`）：提示词与工具 schema 原样保留；十个工具；
  笔记压缩 + 思考回灌主循环。保留和简化的内容见 [docs/MIGRATION.md](docs/MIGRATION.md)。
- 检索（`src/margin/retrieval/`）：BM25（scipy 稀疏矩阵）+ 实体别名 + 可选向量，RRF 融合。
  在真实语料上验证：17,596 个 block，首次建索引约 100 秒，缓存 41MB，之后加载 1.3 秒。
- OpenAI 兼容模型客户端（`src/margin/llm/`），统一 reasoning_content / reasoning 字段。
- 75 个测试：51 个单元测试（`conda run -n margin pytest`）+ 24 个接口 / 集成测试（`pytest -m integration`，连真实 PG），全部通过，ruff 无报错。
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

## 下一步：M1.5 队列切换与对账（PLAN §5.7，在 M2 之前做）

M2 要让 Redis 承担实时推流，所以先把任务队列从 Redis 拆出去。每一步一个小提交，连续推进，作者事后逐个 review；
每步都要 `pytest`、`pytest -m integration`、`ruff check` 通过。

| 步骤 | 内容 | 这一步的完成标准 |
|---|---|---|
| 0. review 收尾 ✅ | 本轮文档（m1_review 五章、PLAN §5.4 / §5.7、D19、STATUS）已提交 | 作者已确认 review 结束 |
| 1. 迁移 0002 | outbox 加 `redeliveries`（默认 0）+ 部分索引 `outbox(sent_at) WHERE status='sent'`；models.py 同步 | 空库 `upgrade head` 成功；已有 M1 数据的库从 0001 升到 0002 数据不丢；`alembic check` 无差异 |
| 2. 接入 RabbitMQ | compose 加 `rabbitmq`（数据卷、固定主机名、健康检查、`consumer_timeout` 1 小时）；`MARGIN_BROKER_URL`；worker 改 broker、删 `visibility_timeout`、开发送确认；`celery[redis]` → `celery`；dispatcher 捕获“拒收 / 未确认” | `docker compose up` 全部健康；提交一道题能跑完；管理界面能看到队列和 4 个预取；改造后的“中转站不可用时退避”测试通过 |
| 3. 对账 `requeue_lost` | 水位线 / 队列为空 / 30 分钟兜底三条规则；补发上限 3 次后判 `delivery_lost`；消费者为 0 不补发 | PLAN §5.7 的 6 个集成测试通过 |
| 4. LISTEN / NOTIFY | `_enqueue_attempt`、`requeue_lost` 里 NOTIFY；dispatcher 专用连接 LISTEN，兜底扫描最长 10 秒 | “提交后 1 秒内投递（不靠兜底扫描）”“事务回滚不投递”两个测试通过 |
| 5. 验收与文档 | 手动故障演练；README、`review/01_运行链路.md`、`review/02_代码导读` 加 M1.5；STATUS 记录 | 见下方结束条件 |

**M1.5 结束条件（全部满足才算完成）：**

1. 自动化：全部单元 / 集成测试通过（M1 的 75 个 + 新增约 9 个），`ruff check` 无报错，`alembic check` 无差异。
2. 故障演练（手动，结果记进 STATUS）：
   - 提交后、worker 领取前**重启 RabbitMQ** → 消息不丢，任务正常完成（验证持久化）；
   - 停掉 worker、提交任务、在管理界面**清空队列**、再启动 worker → 巡检补发，任务完成（验证对账）；
   - 执行中 `docker compose kill worker` → 约 90 秒后判 `lease_expired`，已完成的步骤保留（M1 行为不退化）；
   - 新任务从提交到被投递 **< 1 秒**，dispatcher 空闲时不再每秒查库（验证 LISTEN / NOTIFY）。
3. Redis 不再承担任务队列（代码和 compose 里 worker / dispatcher 不再依赖 redis）。
4. 文档：D19 与实现一致；README 的启动说明、运行链路图更新为 8 个服务；作者 review 完全部提交。

## 再下一步：M2 过程可视化

**开始写代码前必读（作者要求）**：AGENTS.md 的“代码风格”和“测试”两节。要点——
中文注释讲清“为什么”和概念（读者是后端初学者）；**不写防御性代码**（只在模型参数、外部服务、用户输入这些边界处理错误，
不加哈希 / 版本闸门）；简单直接、一个函数做一件事；测试只写单元 / 接口 / 集成，一个测试对应一个会出事故的场景；
小步提交，每块代码量控制在作者一次能 review 完。

M2 范围（PLAN §7）：

1. 两层事件：worker 把思考片段（带 epoch）发到 Redis pub/sub；SSE 同时转发实时片段和持久事件；
   API 改为收到“有新事件”的通知再查库，代替每秒轮询。**通知已定用 PG LISTEN / NOTIFY（D20，做法见 PLAN §5.4）**：
   `add_event` 同事务 NOTIFY，每个 API 进程一条 LISTEN 连接、内存里转发给 SSE，保留低频兜底查询。
2. React 前端（按 D14 选定的简洁风 + 衬线 + 石墨）：提交框、时间线（思考逐字展开、工具卡片、引用批注）、
   刷新后从库里恢复完整历史；联调时逐项检查显示和交互逻辑。
3. Vite 开发代理到 API（同源，不开 CORS）。

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

## 本地环境备忘

- 语料路径在 `.env`，默认指向 `D:/competition/finetune/data/`（只读使用）。
- BM25 缓存在 `.cache/bm25/`（不进仓库），语料变化时删除重建。
- WSL Docker 可用（2026-10-03 验证：`flora` 已在 docker 组；Windows 经 localhost 能连到 WSL 容器端口）。
  注意：WSL 空闲时会自动关机，容器随之停止。跑服务时要保持一个 WSL 终端开着，
  或在 `%UserProfile%\.wslconfig` 里调大 `vmIdleTimeout`。
- Windows 上的脚本经 localhost 连 WSL 里的 PG，发送大于约 20KB 的请求会多约 45ms（转发层，D18）；容器之间没有这个问题。
