"""E1 现状摸底：现有系统（Harness 原样）跑 5 道开放问题 + 5 道数值题。

看模型交什么、引不引用、笔记里有什么。开放问题按“文本”格式提交（open_questions.json 前 5 道）；
数值题取 E80 里开放检索的数值 / 百分比题（有参考答案）。
结果：cache_dir/m25/e1/<题号>.json，以及汇总表 e1_summary.md。E2、E5 用这 10 道题。

用法：
    conda run -n margin --no-capture-output python scripts/experiments/m25/e1_baseline.py \
        --e80-tasks <训练仓库>/eval/sets/h1.1.0-rc/E1.jsonl \
        --e80-refs .cache/e80_20261004/scoring_references.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import HERE, RESULTS, Env, correct, load_e80, parallel, run_task


def e1_tasks(e80_tasks: Path, e80_refs: Path) -> list[dict]:
    opens = json.loads((HERE / "open_questions.json").read_text(encoding="utf-8"))[:5]
    tasks = [{"task_id": q["id"], "question": q["question"], "options": None,
              "answer_format": "text"} for q in opens]
    numeric = [t for t in load_e80(e80_tasks, e80_refs) if t["answer_format"] in ("num", "pct")]
    numeric.sort(key=lambda t: (t["answer_format"] != "pct", t["task_id"]))  # 先取百分比，两类都有
    for t in numeric[:5]:
        tasks.append({"task_id": t["task_id"], "question": t["question"], "options": None,
                      "answer_format": t["answer_format"], "reference": t["reference"]})
    return tasks


def summarize(rows: list[tuple[dict, dict]]) -> str:
    lines = ["| 题 | 格式 | 交了什么 | 对错 | 步数 | cite 成功 / 调用 | 写笔记 | 耗时 |",
             "|---|---|---|---|---|---|---|---|"]
    for task, result in rows:
        steps = result.get("steps", [])
        cites = [s for s in steps if s["tool"] == "cite"]
        grounded = sum(1 for s in cites if s["result"].get("ok"))
        notes = sum(1 for s in steps if s["tool"] == "write_note" and s["result"].get("ok"))
        final = result.get("final") or {}
        answer = (final.get("submitted") or final.get("reason_code") or result.get("violation")
                  or result.get("error"))
        ok = correct(task, result)
        verdict = "-" if ok is None else ("对" if ok else "错")
        lines.append(f"| {task['task_id']} | {task['answer_format']} | {str(answer)[:80]} | "
                     f"{verdict} | {len(steps)} | {grounded} / {len(cites)} | {notes} | "
                     f"{result.get('total_seconds', '-')}s |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e80-tasks", type=Path, required=True)
    parser.add_argument("--e80-refs", type=Path, required=True)
    parser.add_argument("--model", default="DeepSeek")
    args = parser.parse_args()
    env = Env(args.model)
    tasks = e1_tasks(args.e80_tasks, args.e80_refs)
    out = RESULTS / "e1"
    results = parallel(lambda t: run_task(env, t, out / f"{t['task_id']}.json"), tasks)
    table = summarize(list(zip(tasks, results, strict=True)))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "e1_summary.md").write_text(table + "\n", encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
