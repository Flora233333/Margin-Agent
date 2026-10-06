# RC6-C Harness 移植说明

来源：`D:\competition\finetune`，commit `4e2fce1`，Harness 版本 `h1.2.5-rc6c`
（runtime `H1_2_5_RC6C_RUNTIME`，工具 schema `h1.2-open-tool-schemas-v7-rc6c`，prompt `system-v1.2.5-rc6c`）。

原版为了研究可复现，依赖闭包有 37 个模块、约 1.86 万行，其中大部分是历史版本分支、缓存清单、
身份漂移校验。Margin 是**轻量移植**：只保留 RC6-C 实际走的路径，`src/` 共约 2,000 行（其中大量是中文注释）。

## 原样保留（模型接口，不能改）

| 内容 | 位置 | 为什么不能改 |
|---|---|---|
| system 提示词 | `harness/prompts/system_rc6c.txt` | 自训模型在这份提示词下训练 |
| 十个工具的 schema（名称、顺序、描述、参数约束） | `harness/tool_schemas.json`（从原版导出） | 同上；测试保证 Pydantic 参数模型与它一致 |
| 题目渲染格式 | `harness/prompt.py` | 同上 |
| 写笔记即压缩：成功 write_note 后上下文 = [system, 题目, 笔记] | `harness/loop.py` | RC6-C 的核心行为 |
| 思考回灌：assistant 消息带 reasoning_content | `harness/loop.py` | 同上 |
| 笔记门控、无工具调用修复提醒、格式重试 6 次、原地打转 3 次停止 | `harness/loop.py`、`tools/base.py` | 停止条件与原版一致 |
| 工具结果外层格式（含 response_version 字段） | `tools/base.py` | 与训练时看到的格式一致 |
| compute v2（分数次方、函数、result_exact）、date_calc、答案规范化 | `harness/calc.py`、`harness/answers.py` | 计算与评分口径一致 |
| BM25 分词（jieba + 两字切片）、参数 k1=1.5 / b=0.75、RRF k=60 / 窗口 8 | `retrieval/` | 检索效果的主要来源 |

## 简化了什么

| 原版 | Margin | 影响 |
|---|---|---|
| 13 个历史 runtime 版本的分支 | 只有 RC6-C 一条路径 | 无 |
| 版本清单、manifest 哈希、身份漂移校验（fail-closed） | 删除 | 无（研究可复现用的） |
| 每个检索结果的持久查询缓存（SQLite） | 删除，BM25 直接算（毫秒级） | 无 |
| 稀疏索引 1.19GB 缓存 | scipy 稀疏矩阵，缓存 41MB，首建约 100 秒 | 无 |
| 引用匹配器 v2（7 级规范化 + 未解决引用追踪，未修复不许 finalize） | 3 级匹配 + 失败时给出建议原文，不阻止 finalize | 引用失败不再强制修复 |
| 工具内的“第 3 次重复调用报错” | 只保留循环层的“原地打转”检测 | 停止时机相同 |
| 工具结果里的 cache、audit、provenance 等字段 | 删除 | 模型看到的结果少几个辅助字段 |
| prompt token 上限检查（需要 tokenizer） | 删除，超长时由模型接口报错 | 后续如需要再加 |
| 模型调用失败记为 `provider_failure` | 直接抛异常，由后端的重试机制处理 | 失败语义交给服务层 |
| 多工具调用投影（first-tool projection） | 删除，多个调用直接停止 | RC6-C 采样本来就不启用 |

## 和训练环境不一致的地方（修了原版的错）

| 原版 | Margin | 影响 |
|---|---|---|
| `calculate_date` 按月 / 年时也按 `count_from=same_day` 减 1，少算一整个月 / 年 | 按月 / 年不看 `count_from`（工具说明本来就写它只用于 `unit=day`），2026-10-06 修 | 只影响“按月或年 + same_day”的调用；训练仓库的题库日期题都是按日计算，评测分数不受影响。训练仓库要不要同步修由作者决定 |

## 未移植（可选数据源，需要时再补）

| 内容 | 原版来源 | 说明 |
|---|---|---|
| `document_identity`（search_docs 结果里的文档身份：标题、主体、类型、报告期） | `document-identity-v3-rich-context-llm-audited` | 离线 LLM 审核生成的数据。提示词里提到了它，缺失时模型只是少了导航信息 |
| `table_context` / `table_scope_context`（表格的表头来源、口径来源 block） | `table_context_v1`、`table_scope_context` 缓存 | 跨页表格的表头借用信息。提示词说“可能带”，缺失不影响流程 |

补回的方式：作为可选的查找表注入 ToolContext，在 search_in_document / read_section 结果里附加字段。

## 怎么验证没搬坏

- `tests/test_tools.py::test_schema_and_argument_models_agree`：发给模型的 schema 与参数校验一致。
- `tests/test_loop.py`：压缩、回灌、各停止条件的行为。
- M0.5 用真实模型在固定题上跑，与 finetune 侧同模型的结果对比准确率（允许检索细节差异带来的小幅波动）。
