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

## 下一步（M0.5 真实运行）

0. 作者从三种风格（简洁 / 手绘 / 瑞士）、字体（无衬线 / 衬线）、五套配色中选定组合，记入 DECISIONS.md。
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
- WSL Docker 可用（2026-10-03 验证：`flora` 已在 docker 组；Windows 经 localhost 能连到 WSL 容器端口）。
  注意：WSL 空闲时会自动关机，容器随之停止。M1 起 PG/Redis 时要保持一个 WSL 终端开着，
  或在 `%UserProfile%\.wslconfig` 里调大 `vmIdleTimeout`。
