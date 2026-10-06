# Margin 工作约定（给 AI 编程助手和协作者）

这是一个**边做边学**的项目：作者会逐行 review 代码，用来准备 Agent / 后端方向的面试。
所以“看得懂”比“写得巧”重要。

## 每次会话的流程

1. **开始前**读 [STATUS.md](STATUS.md)：当前在哪个里程碑、下一步做什么、有哪些未决问题。
2. 需要背景时再读 [docs/PLAN.md](docs/PLAN.md)（整体计划）和 [docs/DECISIONS.md](docs/DECISIONS.md)（为什么这样选）。
3. **结束前**更新 STATUS.md：完成了什么、下一步、阻塞点。做了技术取舍就在 DECISIONS.md 追加一条。
4. 不要新建其他“进度/交接/报告”类文档，状态只记在 STATUS.md 一个地方。

## 代码风格

- **写中文注释**，解释“为什么”和“这是什么概念”，读者是后端初学者。模块开头用 docstring 说明这个文件负责什么。
- **不写防御性代码**。只在真正的边界处理错误：模型给的参数（要反馈给模型修正）、外部服务调用、用户输入。
  内部函数之间互相信任，不重复校验，不写“理论上不会发生”的分支。
- 简单直接优先：少用元编程、少用一行流技巧、少抽象层。一个函数做一件事。
- `ruff` 行宽 100；提交前 `conda run -n margin ruff check src tests scripts` 必须通过。

## 测试

- 只写企业后端常用的测试：单元测试、接口测试、集成测试（真实 PG/Redis）。
- 前端另有端到端测试（Playwright，`web/app/e2e/`）：真浏览器像用户一样操作页面，后端换成按录制数据回放的假 API，
  只测浏览器里才看得出来的交互（出场顺序、刷新、断线重连等）。改了前端交互后运行 `npm run e2e`。
- **一个测试对应一个“出了问题会造成事故”的场景**，测试名写清楚场景。
- 不写 smoke 脚本、不做哈希/清单一致性校验之类的测试。
- 模型一律用 `tests/fakes.py` 的 FakeLLM，测试不调用真实模型、不联网。
- 每次改代码后运行 `conda run -n margin pytest`。

## 运行环境约定

开发机是 Windows 10 + WSL2（Ubuntu 22.04，Docker 只装在 WSL 里）。代码在 Windows 上编辑，按下表分两层运行：

| 内容 | 在哪运行 | 命令 |
|---|---|---|
| Python 代码、单元测试、脚本 | Windows，conda 环境 `margin`（Python 3.11） | `conda run -n margin pytest` |
| PostgreSQL、Redis | WSL2 的 Docker Compose，端口映射到 localhost | `wsl docker compose up -d postgres redis` |
| FastAPI（开发） | Windows `margin` 环境，热重载，连 localhost 的 PG/Redis | `conda run -n margin --no-capture-output uvicorn ...` |
| Celery worker | Linux：WSL2 或容器（Celery 官方不支持 Windows） | `wsl docker compose up worker` |
| 集成测试（需要真实 PG/Redis） | Windows，先把 PG/Redis 起好 | `conda run -n margin pytest -m integration` |
| 前端 | Windows，Node 24 + Vite | `npm run dev` |
| 前端端到端测试 | Windows，本机 Chrome；假 API 和 Vite 由 Playwright 自动起 | `cd web/app && npm run e2e` |
| 前端设计稿 | 本地浏览器直接打开 `web/design/*.html` | 不发布到任何在线平台 |
| 自训模型 | 实验室服务器 vLLM，OpenAI 兼容接口 | `.env` 里配 `MARGIN_LLM_BASE_URL` |
| CI | GitHub Actions（ubuntu） | push 时自动跑 |

### Python 环境：conda 环境 `margin`

- 所有 Python 命令都通过 `conda run -n margin ...` 执行。**不用裸 `python` / `pip`**（Bash 里的裸 `python`
  会落到 miniconda 的 base 环境），不用 base、不用比赛项目的 AFAC2026，也不用 `pip install --user`。
- 改代码前先确认环境：`conda run -n margin python -c "import sys; print(sys.prefix)"` 应输出
  `...\.conda\envs\margin`。
- 分工：conda 只提供 Python 解释器和环境变量（`environment.yml`）；Python 依赖以 `pyproject.toml` 为准，用 pip 装：

  ```powershell
  conda env create -f environment.yml                       # 第一次：建环境
  conda run -n margin python -m pip install -e . --group dev # 装项目（可编辑模式）+ 开发工具
  conda run -n margin python -m pip check                   # 检查依赖冲突
  ```

- 新增依赖：先写进 `pyproject.toml`，再重跑上面的 pip install；并在 DECISIONS.md 写明为什么需要。
  不直接 `pip install 某包` 而不写进 pyproject，否则别人（和 CI、Docker）复现不出来。
- 环境变量 `PYTHONNOUSERSITE=1`、`PYTHONIOENCODING=utf-8` 已写在 `environment.yml`，不要删除或覆盖。
- 需要实时看到输出的命令（脚本、uvicorn、Rich 输出）加 `--no-capture-output`。
- CI 和 Docker 镜像不用 conda：用官方 Python 镜像 + 同一份 pyproject 的 `pip install`。

### 其他

- 前端依赖只装在 `web/` 下各项目自己的 `node_modules`，不做 `npm install -g`。
- Windows 下读写文本文件一律显式 `encoding="utf-8"`（默认是 GBK）。
- 仓库统一 LF 换行（`.gitattributes`），容器和 CI 才不会出问题。

## 数据与密钥

- 语料、索引、缓存不进仓库（见 `.gitignore`），路径在 `.env` 里配置。原始语料只读，不修改。
- 密钥只放 `.env`，绝不打印、提交或写进日志。

## Harness 的特殊约束

`src/margin/harness/prompts/system_rc6c.txt` 和 `tool_schemas.json` 是**模型接口**：
自训模型在这套提示词和工具说明下训练过，改动会改变模型行为。需要改时先在 DECISIONS.md 记录，
并说明是否需要重新评测。移植时做了哪些简化见 [docs/MIGRATION.md](docs/MIGRATION.md)。

## Git

- 小步提交，提交信息用中文或英文都可以，说清楚“做了什么、为什么”。
- 不提交 `.env`、数据、索引、`node_modules`。
