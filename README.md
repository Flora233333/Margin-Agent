# Margin

**可追溯证据的金融长文档 Agent 服务。** 名字取双关：页边（margin）批注证据，也是金融里的保证金/利润率。

用户对年报、债券募集说明书、保险条款等长文档提问（跨文档、跨年度、需要计算），
Agent 自己检索、阅读原文、核对口径、计算，并给每个结论标注原文出处。

> 当前阶段：M0.5。Harness 已接通真实模型和三路检索（向量存 PostgreSQL），后端服务尚未开始。进度见 [STATUS.md](STATUS.md)。

## 结构

```text
src/margin/
  harness/      Agent 核心（移植自 RC6-C）：提示词、十个工具、主循环
  retrieval/    检索：BM25 + 实体别名 + 向量（pgvector，可选），RRF 融合
  llm/          OpenAI 兼容模型客户端（DeepSeek / vLLM 自训模型）
tests/          pytest，假模型 FakeLLM 驱动，不调用真实模型
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

开启向量检索（第三路）还需要三步，见 docs/DECISIONS.md D15：

```bash
wsl -d Ubuntu-22.04 -- bash -lc "cd /mnt/d/competition/margin-agent && docker compose up -d --wait postgres"
pwsh scripts/start_embedding.ps1        # 另开一个终端，保持运行
conda run -n margin --no-capture-output python scripts/import_vectors.py .cache/chroma/rc6-local-qwen3emb06b-q8_0-v2/chroma   # 只需一次
```

注意：WSL 空闲时会自动关机，PG 随之停止；跑长任务时保持一个 WSL 终端开着。

## 文档

- [docs/PLAN.md](docs/PLAN.md)：后端完整计划（技术栈、架构、里程碑）
- [docs/DECISIONS.md](docs/DECISIONS.md)：技术决策记录
- [docs/MIGRATION.md](docs/MIGRATION.md)：RC6-C 移植说明（保留了什么、简化了什么）
- [AGENTS.md](AGENTS.md)：给 AI 编程助手的工作约定
