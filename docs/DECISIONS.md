# 技术决策记录

每条：日期、决定、为什么、考虑过的替代方案。新决策追加在末尾，旧决策被推翻时不删除，写一条新的说明。

---

### D1 · 2026-10-03 · 后端独立成新仓库

- **决定**：从 `D:\competition\finetune` 拆出，新建 `margin-agent` 仓库，只移植 RC6-C Harness。
- **为什么**：finetune 有 3 万行研究代码、密钥文件和比赛数据，不适合公开；独立仓库可以放进简历链接，代码量小便于 review。
- **替代**：在 finetune 内开发（2026-09-17 文档的建议），已放弃。

### D2 · 2026-10-03 · 后端语言用 Python，框架用 FastAPI

- **为什么**：Agent 岗主流是 Python，Harness 本身是 Python；FastAPI 原生异步、SSE 自然、Pydantic 校验。
- **替代**：Java/Spring（两种语言 + RPC，无新价值）；Django（全家桶以同步为主，后台和模板用不上）。

### D3 · 2026-10-03 · 队列用 Celery + Redis，不用 RabbitMQ / Kafka

- **为什么**：Redis 同时承担队列、限流、通知、缓存；任务正确性由数据库租约保证，队列只是“唤醒信号”，可替换。
  Kafka 是事件流平台，适合高吞吐多消费者，不适合少量长任务的确认与取消。
- **替代**：RabbitMQ（只讲原理，不实现切换）。

### D4 · 2026-10-03 · 不用 LangGraph

- **为什么**：需要精确控制每步状态、与训练轨迹对齐；WeSeeker 项目已覆盖 LangGraph 经验。

### D5 · 2026-10-03 · Harness 轻量移植，模型接口原样保留

- **决定**：提示词、工具 schema、消息拼接、笔记压缩、思考回灌原样保留；数据加载、缓存、版本校验重写或删除。详见 MIGRATION.md。
- **为什么**：自训模型依赖模型接口；其余部分是研究期的防御代码，增加理解成本。

### D6 · 2026-10-03 · 依赖管理用 uv，不用 conda

- **为什么**：企业 Python 项目的标准做法（pyproject + lock 文件 + .venv），Docker 镜像里也直接用 uv。新项目不与 AFAC2026 环境混用。

### D7 · 2026-10-03 · BM25 用 scipy 稀疏矩阵自己实现

- **新增依赖**：`numpy`、`scipy`。
- **为什么**：rank_bm25 把每个 block 存成 Python 字典，1.76 万个 block 内存很大；稀疏矩阵缓存只有 41MB、加载 1.3 秒。公式与 rank_bm25 一致。

### D8 · 2026-10-03 · 模型调用失败在 Harness 内直接抛异常

- **为什么**：重试属于服务层（attempt 级重试 + 网关退避），Harness 不吞异常，失败语义只在一处处理。

### D9 · 2026-10-03 · 测试只写企业风格的单元 / 接口 / 集成测试

- **为什么**：作者的要求。每个测试对应一个会出事故的场景；不写 smoke 脚本和哈希校验。

### D10 · 2026-10-03 · 运行环境分两层：Windows 跑 Python，WSL2 Docker 跑基础服务

- **决定**：Python 代码、测试、FastAPI 开发服务器在 Windows 的 `.venv` 里跑；PostgreSQL、Redis、Celery worker 在 WSL2 的 Docker 里跑，通过 localhost 端口互通。细则见 AGENTS.md“运行环境约定”。
- **为什么**：Celery 官方不支持 Windows；Docker 只装在 WSL 里。日常编辑、调试、跑单元测试留在 Windows 最顺手。
- **替代**：全部在 WSL 里开发（仓库放在 WSL 文件系统 + VS Code Remote）。更接近线上 Linux 环境，但与当前 Windows 上的编辑和 AI 助手工作流不一致，暂不采用。

### D11 · 2026-10-03 · 前端设计稿只在本地运行

- **决定**：设计稿放 `web/design/`，用浏览器直接打开，不发布到任何在线平台。
