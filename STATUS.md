# 当前状态

更新：2026-10-03

## 所在阶段：M0 完成，下一步 M0.5

计划全文见 [docs/PLAN.md](docs/PLAN.md)。

## 已完成

- 仓库骨架：conda 环境 `margin`（`environment.yml`）+ pyproject、ruff、pytest、`.env.example`、
  工作约定 AGENTS.md。
- RC6-C Harness 轻量移植（`src/margin/harness/`）：提示词与工具 schema 原样保留；十个工具；
  笔记压缩 + 思考回灌主循环。保留和简化的内容见 [docs/MIGRATION.md](docs/MIGRATION.md)。
- 检索（`src/margin/retrieval/`）：BM25（scipy 稀疏矩阵）+ 实体别名 + 可选向量，RRF 融合。
  在真实语料上验证：17,596 个 block，首次建索引约 100 秒，缓存 41MB，之后加载 1.3 秒。
- OpenAI 兼容模型客户端（`src/margin/llm/`），统一 reasoning_content / reasoning 字段。
- 44 个测试全部通过（`conda run -n margin pytest`），ruff 无报错。
- 前端设计稿第二版 `web/design/`，只在本地打开 `preview.html`：
  三栏布局（历史 / 对话 / 来源）；过程区与回答分层；逐条展开的动效；
  四套配色（墨青 / 石墨 / 藏青 / 松烟）× 深浅色；简洁风 + 手绘风（`sketch.css`）。
  右下角“设计评审”面板切换，也可用地址参数，如 `preview.html?style=sketch&palette=navy&theme=dark`。
- 运行环境约定写入 AGENTS.md（D10）。

## 下一步（M0.5 真实运行）

0. 作者选定设计稿的风格和配色，记入 DECISIONS.md。
1. 在 `.env` 填写模型配置（DeepSeek 或 vLLM 自训模型），用 `scripts/run_episode.py` 跑 3 类题各一道。
2. 验证 DeepSeek 是否接受历史消息里的 `reasoning_content`（见“未决问题”1）。
3. LLM 客户端加流式输出（`stream=True`），为 M2 的逐字思考展示做准备。
4. 进入 M1：FastAPI + PostgreSQL + Celery/Redis 最小闭环。

## 未决问题

1. DeepSeek API 若拒绝历史消息里的 `reasoning_content`，需要在客户端发送前去掉（只对自训模型保留）。
2. 向量检索未在真实环境验证：需要本地 embedding 服务 + `artifacts/indexes/chroma/h1.1.0-rc` 索引。
3. 文档身份 / 表格上下文两类结果增强未移植，先观察自训模型在缺少它们时的表现。

## 本地环境备忘

- 语料路径在 `.env`，默认指向 `D:/competition/finetune/data/`（只读使用）。
- BM25 缓存在 `.cache/bm25/`（不进仓库），语料变化时删除重建。
- WSL 里的 Docker 已安装并在运行，但用户 `flora` 不在 `docker` 组，`docker ps` 报 permission denied。
  M1 之前由作者执行：`wsl -d Ubuntu-22.04 -- sudo usermod -aG docker flora`，再 `wsl --shutdown`。
