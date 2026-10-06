"""E4 理解题目：同一个模型、独立提示词，从问题里得出标题、结论说明和答案格式。

题目：E80 的 80 道题（去掉选项、只给题干）+ open_questions.json 的 10 道开放问题。
期望格式：E80 的单选 / 多选题去掉选项后，问的是“哪些说法正确”，期望 text；其他按题集标注。
通过标准（PLAN §5.8）：格式一致 ≥ 90%；耗时中位数 < 5 秒；标题人工看能不能用。
结果：cache_dir/m25/e4.json 和给人读的 e4_report.md。

用法：
    conda run -n margin --no-capture-output python scripts/experiments/m25/e4_understand.py \
        --e80-tasks <训练仓库>/eval/sets/h1.1.0-rc/E1.jsonl \
        --e80-refs .cache/e80_20261004/scoring_references.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from common import HERE, RESULTS, Env, describe_error, load_e80, parallel, plain_chat

PROMPT = (HERE / "prompts" / "understand_draft.txt").read_text(encoding="utf-8")
FORMATS = {"num", "pct", "date", "tf", "text"}


def expected(answer_format: str) -> str:
    return "text" if answer_format in ("mcq", "multi") else answer_format


def understand(env: Env, item: dict[str, Any]) -> dict[str, Any]:
    try:
        messages = [{"role": "system", "content": PROMPT},
                    {"role": "user", "content": item["question"]}]
        reply = plain_chat(env.llm, messages, max_tokens=2000)
    except Exception as exc:  # 网关报错：记一条，按产品的退回规则处理（标题取前 20 字、格式 text）
        return {**item, "error": describe_error(exc), "parsed": None, "seconds": None}
    match = re.search(r"\{.*\}", reply["content"], re.S)  # 模型偶尔会包一层 ```json
    try:
        parsed = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        parsed = None
    if parsed and parsed.get("answer_format") not in FORMATS:
        parsed = None
    return {**item, "parsed": parsed, "raw": reply["content"], "seconds": reply["seconds"],
            "usage": reply["usage"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e80-tasks", type=Path, required=True)
    parser.add_argument("--e80-refs", type=Path, required=True)
    parser.add_argument("--model", default="DeepSeek")
    args = parser.parse_args()
    env = Env(args.model)
    e80 = load_e80(args.e80_tasks, args.e80_refs)
    items = [{"id": t["task_id"], "question": t["question"], "source_format": t["answer_format"],
              "expected": expected(t["answer_format"])} for t in e80]
    opens = json.loads((HERE / "open_questions.json").read_text(encoding="utf-8"))
    items += [{"id": q["id"], "question": q["question"], "source_format": "open",
               "expected": q["expected_format"]} for q in opens]
    results = parallel(lambda item: understand(env, item), items)
    RESULTS.mkdir(parents=True, exist_ok=True)
    dump = json.dumps(results, ensure_ascii=False, indent=1)
    (RESULTS / "e4.json").write_text(dump, encoding="utf-8")

    ok = [r for r in results if r["parsed"] and r["parsed"]["answer_format"] == r["expected"]]
    failed = [r for r in results if not r["parsed"]]
    seconds = [r["seconds"] for r in results if r["seconds"] is not None]
    by_source = Counter(
        (r["source_format"], r["parsed"]["answer_format"] if r["parsed"] else "失败")
        for r in results)
    lines = ["# E4 理解题目", "",
             f"- 格式一致 {len(ok)}/{len(results)}（{len(ok) / len(results):.0%}）；"
             f"解析失败 {len(failed)}",
             f"- 耗时中位数 {statistics.median(seconds):.1f}s，最长 {max(seconds):.1f}s", "",
             "| 题集格式 | 判成 | 题数 |", "|---|---|---|"]
    lines += [f"| {src} | {got} | {n} |" for (src, got), n in sorted(by_source.items())]
    lines += ["", "| 题 | 期望 | 判成 | 标题 | 说明 | 耗时 | 问题 |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        p = r["parsed"] or {}
        mark = "" if p.get("answer_format") == r["expected"] else " ✗"
        lines.append(f"| {r['id']} | {r['expected']} | {p.get('answer_format', '失败')}{mark} | "
                     f"{p.get('title', '')} | {p.get('label', '')} | {r['seconds']}s | "
                     f"{r['question'][:60]} |")
    report = "\n".join(lines)
    (RESULTS / "e4_report.md").write_text(report + "\n", encoding="utf-8")
    print("\n".join(lines[:6]))


if __name__ == "__main__":
    main()
