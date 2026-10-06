"""E6 理解题目的格式判断：现行提示词和候选提示词在同一批题上对比，只看 answer_format。

为什么要测：判成 num / pct / date / tf 的题，结论才会以醒目的大字显示；
数值题判成 text 就只剩一段文字。
判错成具体格式是安全的：finalize 按猜的格式收不下时自动按文本收（D25）。
所以重点看“具体格式题判成了别的”（召回），“text 判成具体格式”只记录不扣分。

走产品的 margin.understand.understand()（解析、退回规则都和线上一样），只是换掉它读的提示词。
题目（训练仓库的题集，按问题原句去重）：
    E1：除选择题外全部（tf / num / pct / date / text）+ 30 道选择题
    E2：num / pct / date 全部 + 10 道选择题
    人工整理题集：num / pct / date 各 25 道
    open_questions.json 的 10 道开放问题
选择题只给题干（去掉选项），问的是“哪些说法正确”，期望 text。
结果：cache_dir/m25/e6.json 和给人读的 e6_report.md。

用法：
    conda run -n margin --no-capture-output python scripts/experiments/m25/e6_formats.py \
        --bank D:/competition/finetune
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from common import HERE, RESULTS, RetryingLLM, load_jsonl, parallel, settings

from margin import understand as understanding
from margin.assembly import build_llm

CANDIDATES = {
    "现行": Path(understanding.__file__).parent / "prompts" / "understand.txt",
    "候选": HERE / "prompts" / "understand_v2.txt",
}
SPECIFIC = ["num", "pct", "date", "tf"]
CHOICE = {"mcq", "multi"}
CURATED = "artifacts/audits/financial_qa_curated_output_20260815/output/tasks.jsonl"


def expected(answer_format: str) -> str:
    return "text" if answer_format in CHOICE else answer_format


def item(source: str, task: dict[str, Any]) -> dict[str, Any]:
    return {"id": task["task_id"], "source": source, "question": task["question"],
            "source_format": task["answer_format"], "expected": expected(task["answer_format"])}


def build_items(bank: Path) -> list[dict[str, Any]]:
    rng = random.Random(0)
    e1 = [row["task_snapshot"] for row in load_jsonl(bank / "eval/sets/h1.1.0-rc/E1.jsonl")]
    e2 = [row["task_snapshot"] for row in load_jsonl(bank / "eval/sets/h1.1.0-rc/E2.jsonl")]
    curated = load_jsonl(bank / CURATED)

    picked = [("E1", t) for t in e1 if t["answer_format"] not in CHOICE]
    picked += [("E1", t) for t in rng.sample([t for t in e1 if t["answer_format"] in CHOICE], 30)]
    picked += [("E2", t) for t in e2 if t["answer_format"] in ("num", "pct", "date")]
    picked += [("E2", t) for t in rng.sample([t for t in e2 if t["answer_format"] in CHOICE], 10)]
    for fmt in ("num", "pct", "date"):
        pool = [t for t in curated if t["answer_format"] == fmt]
        picked += [("整理", t) for t in rng.sample(pool, 25)]

    seen: set[str] = set()
    items = []
    for source, task in picked:
        if task["question"] not in seen:
            seen.add(task["question"])
            items.append(item(source, task))
    opens = json.loads((HERE / "open_questions.json").read_text(encoding="utf-8"))
    items += [{"id": q["id"], "source": "开放", "question": q["question"], "source_format": "open",
               "expected": q["expected_format"]} for q in opens]
    return items


def judge(llm: RetryingLLM, entry: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    result = understanding.understand(llm, entry["question"])
    # 产品代码把失败吞掉、退回“前 20 字 + text”；这里认出退回的结果，单独计数
    failed = result == understanding.fallback(entry["question"])
    return {**entry, "got": "失败" if failed else result.answer_format, "title": result.title,
            "label": result.label, "seconds": round(time.monotonic() - started, 1)}


def summarize(name: str, results: list[dict[str, Any]]) -> list[str]:
    exact = sum(r["got"] == r["expected"] for r in results)
    seconds = [r["seconds"] for r in results]
    lines = [f"## {name}", "",
             f"- 和期望一致 {exact}/{len(results)}（{exact / len(results):.0%}）；"
             f"失败 {sum(r['got'] == '失败' for r in results)}；"
             f"耗时中位数 {statistics.median(seconds):.1f}s", "",
             "| 期望 | 题数 | 判对 | 召回 | 判成（其余） |", "|---|---|---|---|---|"]
    for fmt in [*SPECIFIC, "text"]:
        group = [r for r in results if r["expected"] == fmt]
        if not group:
            continue
        hit = sum(r["got"] == fmt for r in group)
        others = Counter(r["got"] for r in group if r["got"] != fmt)
        rest = "、".join(f"{k} {v}" for k, v in others.most_common())
        lines.append(f"| {fmt} | {len(group)} | {hit} | {hit / len(group):.0%} | {rest} |")
    return lines


def misses(results: dict[str, list[dict[str, Any]]]) -> list[str]:
    """任何一版判得和期望不同的题，两版并排列出，方便看是题集标注的问题还是提示词的问题。"""
    names = list(results)
    lines = ["## 判得和期望不同的题", "",
             "| 题 | 来源 | 期望 | " + " | ".join(names) + " | 问题 |",
             "|---|---|---|" + "---|" * len(names) + "---|"]
    for rows in zip(*results.values(), strict=True):
        if all(r["got"] == r["expected"] for r in rows):
            continue
        first = rows[0]
        question = first["question"].replace("|", "/").replace("\n", " ")[:90]
        got = " | ".join(r["got"] + ("" if r["got"] == r["expected"] else " ✗") for r in rows)
        lines.append(f"| {first['id']} | {first['source']} | {first['expected']} | {got} | "
                     f"{question} |")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True, help="训练仓库根目录（读题集）")
    parser.add_argument("--model", default="DeepSeek")
    args = parser.parse_args()
    llm = RetryingLLM(build_llm(settings, args.model))
    items = build_items(args.bank)
    print(f"[题目] {len(items)} 道：{dict(Counter(i['expected'] for i in items))}", flush=True)

    results: dict[str, list[dict[str, Any]]] = {}
    for name, path in CANDIDATES.items():
        # understand() 每次调用时读模块里的 PROMPT，换掉它就是换提示词；两版依次跑，不会混用
        understanding.PROMPT = path.read_text(encoding="utf-8")
        results[name] = parallel(lambda entry: judge(llm, entry), items)
        print(f"[完成] {name}", flush=True)

    RESULTS.mkdir(parents=True, exist_ok=True)
    dump = json.dumps(results, ensure_ascii=False, indent=1)
    (RESULTS / "e6.json").write_text(dump, encoding="utf-8")
    lines = ["# E6 理解题目的格式判断", ""]
    for name, rows in results.items():
        lines += [*summarize(name, rows), ""]
    lines += misses(results)
    (RESULTS / "e6_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
