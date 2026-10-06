"""E3 引用提醒：产品模式下“提醒一次、第二次照收”会不会让答题变差、变慢。

规则（D23）：任务要求引用、还没有任何成功的 cite、这次执行还没提醒过时，finalize 返回工具错误
citation_required；模型第二次提交（补没补引用都一样）照收，没有引用的记为 uncited。
这里把规则包在 finalize 外面（实验用），不改 harness 的代码和工具说明。

E80 按固定种子抽 20 题（带选项，原样），提醒开 / 关各跑一遍。
通过标准（PLAN §5.8）：答对数不少于关闭时减 1；平均多出的调用 ≤ 2 次。
结果：cache_dir/m25/e3/{off,on}/<题号>.json 和 e3_report.md。

用法：
    conda run -n margin --no-capture-output python scripts/experiments/m25/e3_cite_reminder.py \
        --e80-tasks <训练仓库>/eval/sets/h1.1.0-rc/E1.jsonl \
        --e80-refs .cache/e80_20261004/scoring_references.jsonl
"""

from __future__ import annotations

import argparse
import random
import statistics
from pathlib import Path
from typing import Any

from common import RESULTS, Env, correct, load_e80, parallel, run_task

from margin.harness.tools import TOOLS
from margin.harness.tools.actions import finalize
from margin.harness.tools.base import ToolContext, ToolError

SEED = 2026
HINT = ("cite the source text that supports your answer before finalizing; "
        "if no quotable source text exists, call finalize again")


def reminder_tools() -> dict:
    """每道题一份新的工具表：提醒过没有，记在这一题自己的闭包里。"""
    reminded = False

    def finalize_with_reminder(ctx: ToolContext, args: Any) -> dict[str, Any]:
        nonlocal reminded
        if ctx.state.searched and not ctx.state.citations and not reminded:
            reminded = True
            raise ToolError("citation_required", "no successful cite call in this episode", HINT)
        return finalize(ctx, args)

    return {**TOOLS, "finalize": (finalize_with_reminder, TOOLS["finalize"][1])}


def stats(result: dict[str, Any]) -> dict[str, Any]:
    steps = result.get("steps", [])
    reminded = any(s["tool"] == "finalize" and s["result"].get("error") == "citation_required"
                   for s in steps)
    cites = sum(1 for s in steps if s["tool"] == "cite" and s["result"].get("ok"))
    return {"calls": len(result.get("usage", [])), "reminded": reminded, "cites": cites,
            "finalized": (result.get("final") or {}).get("name") == "finalize"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e80-tasks", type=Path, required=True)
    parser.add_argument("--e80-refs", type=Path, required=True)
    parser.add_argument("--model", default="DeepSeek")
    args = parser.parse_args()
    env = Env(args.model)
    tasks = random.Random(SEED).sample(load_e80(args.e80_tasks, args.e80_refs), 20)

    def run(job: tuple[str, dict]) -> dict:
        mode, task = job
        registry = env.registry(task, tools=reminder_tools() if mode == "on" else None)
        return run_task(env, task, RESULTS / "e3" / mode / f"{task['task_id']}.json", registry)

    jobs = [(mode, task) for task in tasks for mode in ("off", "on")]
    results = dict(zip([(m, t["task_id"]) for m, t in jobs], parallel(run, jobs), strict=True))

    lines = ["# E3 引用提醒", "",
             "| 题 | 格式 | 关：对错 / 调用 / 引用 | 开：对错 / 调用 / 引用 / 提醒 |",
             "|---|---|---|---|"]
    summary = {}
    for mode in ("off", "on"):
        rows = [(t, results[(mode, t["task_id"])]) for t in tasks]
        summary[mode] = {
            "correct": sum(1 for t, r in rows if correct(t, r)),
            "errors": sum(1 for _, r in rows if "error" in r),
            "calls": statistics.mean(stats(r)["calls"] for _, r in rows if "error" not in r),
            "uncited": sum(1 for _, r in rows if stats(r)["finalized"] and not stats(r)["cites"]),
            "reminded": sum(1 for _, r in rows if stats(r)["reminded"]),
        }
    for t in tasks:
        off, on = results[("off", t["task_id"])], results[("on", t["task_id"])]
        so, sn = stats(off), stats(on)
        mark = {True: "对", False: "错", None: "-"}
        lines.append(f"| {t['task_id']} | {t['answer_format']} | "
                     f"{mark[correct(t, off)]} / {so['calls']} / {so['cites']} | "
                     f"{mark[correct(t, on)]} / {sn['calls']} / {sn['cites']} / "
                     f"{'是' if sn['reminded'] else ''} |")
    lines.append("")
    for mode, s in summary.items():
        lines.append(f"- {mode}：答对 {s['correct']}/20，网关失败 {s['errors']}，"
                     f"平均调用 {s['calls']:.1f}，交答案时没有引用 {s['uncited']} 题，"
                     f"被提醒 {s['reminded']} 题")
    report = "\n".join(lines)
    (RESULTS / "e3_report.md").write_text(report + "\n", encoding="utf-8")
    print("\n".join(lines[-2:]))


if __name__ == "__main__":
    main()
