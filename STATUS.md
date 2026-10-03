# 当前状态

更新：2026-10-04

## 所在阶段：M0.5 基本完成（向量检索待部署），下一步 M1

计划全文见 [docs/PLAN.md](docs/PLAN.md)。

## 已完成

- 仓库骨架：conda 环境 `margin`（`environment.yml`）+ pyproject、ruff、pytest、`.env.example`、
  工作约定 AGENTS.md。
- RC6-C Harness 轻量移植（`src/margin/harness/`）：提示词与工具 schema 原样保留；十个工具；
  笔记压缩 + 思考回灌主循环。保留和简化的内容见 [docs/MIGRATION.md](docs/MIGRATION.md)。
- 检索（`src/margin/retrieval/`）：BM25（scipy 稀疏矩阵）+ 实体别名 + 可选向量，RRF 融合。
  在真实语料上验证：17,596 个 block，首次建索引约 100 秒，缓存 41MB，之后加载 1.3 秒。
- OpenAI 兼容模型客户端（`src/margin/llm/`），统一 reasoning_content / reasoning 字段。
- 47 个测试全部通过（`conda run -n margin pytest`），ruff 无报错。
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
4. 向量检索（方案待作者审核）：必须对齐 RC6-C 当前用的索引 `rc6-local-qwen3emb06b-q8_0-v2`
   （RL 环境服务 `rc6c_env_server.py` 和 E80 都用它），不用旧的 `h1.1.0-rc`（LM Studio 建的）。
   - v2 只在实验室服务器上（`indexes/rc6-local-qwen3emb06b-q8_0-v2`），本机没有，需要拷回来。
   - 本机 GGUF 的 sha256 与 v2 记录一致（06507c7b…）；llama.cpp 固定提交 a25c9865；
     服务参数 `--pooling last --embd-normalize 2 -b 8192 -ub 8192`，模型名 / 端口照 v2（18082）。
   - 查询格式已对齐 v2：指令 + 空格 + 查询（之前少一个空格）。
5. 进入 M1：FastAPI + PostgreSQL + Celery/Redis 最小闭环。

## 未决问题

1. ~~DeepSeek 是否拒绝历史里的 `reasoning_content`~~：学校网关上不拒绝（2026-10-04 实测）。
   若以后接 DeepSeek 官方 API 再验证一次。
2. 向量检索未在真实环境验证：需要本地 embedding 服务 + `artifacts/indexes/chroma/h1.1.0-rc` 索引。
3. 文档身份 / 表格上下文两类结果增强未移植，先观察自训模型在缺少它们时的表现。

## 本地环境备忘

- 语料路径在 `.env`，默认指向 `D:/competition/finetune/data/`（只读使用）。
- BM25 缓存在 `.cache/bm25/`（不进仓库），语料变化时删除重建。
- WSL Docker 可用（2026-10-03 验证：`flora` 已在 docker 组；Windows 经 localhost 能连到 WSL 容器端口）。
  注意：WSL 空闲时会自动关机，容器随之停止。M1 起 PG/Redis 时要保持一个 WSL 终端开着，
  或在 `%UserProfile%\.wslconfig` 里调大 `vmIdleTimeout`。
