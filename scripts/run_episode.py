"""用真实语料和真实模型跑一道题，打印每一轮的思考、工具调用和结果。

用法：
    conda run -n margin --no-capture-output python scripts/run_episode.py "问题" [--format num]
        [--option A=... --option B=...]
    conda run -n margin --no-capture-output python scripts/run_episode.py "问题" --search-only
        # 只测检索，不调模型
    ... --model GLM        # 临时换模型（默认读 MARGIN_LLM_MODEL）

配置从 .env 读取（见 .env.example）。首次运行会构建 BM25 索引（几分钟），之后从缓存加载。

学校网关是公用的，可用模型时好时坏：
主用 DeepSeek（v4.1-flash），不可用时用 --model GLM（glm5.3-flash）。
一道题从头到尾只用一个模型，不在中途自动切换，否则结果分不清是哪个模型做的。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from margin.harness import build_registry, run_episode
from margin.llm import OpenAICompatibleClient
from margin.retrieval import Corpus, HybridRetriever
from margin.retrieval.alias import AliasCatalog
from margin.retrieval.dense import DenseSearcher, EmbeddingClient


def load_dotenv(path: Path = Path(".env")) -> None:
    """极简 .env 读取：KEY=VALUE，忽略空行和 # 注释。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def build_retriever() -> tuple[Corpus, HybridRetriever]:
    started = time.monotonic()
    corpus = Corpus.load(Path(os.environ["MARGIN_BLOCKS_PATH"]),
                         Path(os.environ["MARGIN_MANIFEST_PATH"]))
    aliases = AliasCatalog.load(Path(os.environ["MARGIN_ALIAS_PATH"]))
    dense = None
    if os.environ.get("MARGIN_CHROMA_PATH"):
        embedder = EmbeddingClient(os.environ["MARGIN_EMBEDDING_BASE_URL"],
                                   os.environ["MARGIN_EMBEDDING_MODEL"])
        dense = DenseSearcher(os.environ["MARGIN_CHROMA_PATH"], embedder)
    cache = Path(os.environ.get("MARGIN_CACHE_DIR", ".cache")) / "bm25"
    retriever = HybridRetriever.create(corpus, cache_dir=cache, aliases=aliases, dense=dense)
    print(f"[检索器就绪] {len(corpus.blocks)} 个 block，{len(corpus.by_doc)} 个文档，"
          f"向量检索={'开' if dense else '关'}，耗时 {time.monotonic() - started:.1f}s")
    return corpus, retriever


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--format", default="text", help="答案类型：num/pct/tf/mcq/multi/date/text")
    parser.add_argument("--option", action="append", default=[], help="选项，如 A=甲公司更高")
    parser.add_argument("--search-only", action="store_true")
    parser.add_argument("--model", help="模型名，如 DeepSeek / GLM；不填则用 MARGIN_LLM_MODEL")
    args = parser.parse_args()
    load_dotenv()
    model = args.model or os.environ.get("MARGIN_LLM_MODEL")

    corpus, retriever = build_retriever()
    if args.search_only:
        for row in retriever.search_docs(args.question)["results"]:
            print(row["rank"], row["doc_id"], row["best_block_id"], row["contributions"])
        return

    if not model:
        parser.error("请用 --model 指定模型，或在 .env 里设置 MARGIN_LLM_MODEL")
    options = dict(item.split("=", 1) for item in args.option) or None
    task = {"question": args.question, "options": options, "answer_format": args.format}
    print(f"模型：{model}")
    llm = OpenAICompatibleClient(os.environ["MARGIN_LLM_BASE_URL"], model,
                                 os.environ.get("MARGIN_LLM_API_KEY", ""))
    trace = run_episode(task, llm, build_registry(task, corpus, retriever))

    for step in trace.steps:
        print(f"\n=== 第 {step.turn} 轮 · {step.tool_name}")
        if step.reasoning:
            print("[思考]", step.reasoning[:500])
        print("[参数]", step.arguments)
        print("[结果]", json.dumps(step.result, ensure_ascii=False)[:500])
    print("\n最终：", trace.final, "| 停止原因：", trace.violation)


if __name__ == "__main__":
    main()
