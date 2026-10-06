"""E5 一次交齐：finalize 同时交短答案和带 [n] 的回答正文，和“finalize + 撰写（E2）”对照。

只在实验里换工具说明，不动 harness/prompts/ 和 tool_schemas.json（D23）：
    - finalize 多一个必填参数 answer_text（给用户看的回答，事实后面用 [n] 标出依据）；
    - cite 成功时结果里多一个 citation_no（第几条成功的引用），模型据此写 [n]。
系统提示词不变。不经训练的模型能不能一次交齐，结果给以后的 RL 定目标，不直接上线。
题目：E1 的 10 道题。结果：cache_dir/m25/e5/<题号>.json 和 e5_report.md（和 E2 并列）。

用法：
    conda run -n margin --no-capture-output python scripts/experiments/m25/e5_finalize_v2.py \
        --e80-tasks <训练仓库>/eval/sets/h1.1.0-rc/E1.jsonl \
        --e80-refs .cache/e80_20261004/scoring_references.jsonl
"""

from __future__ import annotations

import argparse
import copy
import statistics
from pathlib import Path
from typing import Any

from common import RESULTS, Env, correct, parallel, run_task
from e1_baseline import e1_tasks
from evidence import check, normalize_steps, package
from pydantic import Field

from margin.harness.tools import TOOLS
from margin.harness.tools.actions import finalize
from margin.harness.tools.args import FinalizeArgs
from margin.harness.tools.base import TOOL_SCHEMAS, ToolContext
from margin.harness.tools.reading import cite

ANSWER_TEXT = ("给用户看的完整回答（中文，300 字以内）：第一句直接回答问题，随后写依据，"
               "每个事实后面用 [n] 标出依据，n 是 cite 成功时返回的 citation_no；"
               "数字只用原文或 compute 结果里的；没有成功的 cite 时不要写 [n]。")


class FinalizeV2Args(FinalizeArgs):
    answer_text: str = Field(min_length=1, max_length=2000)


def finalize_v2(ctx: ToolContext, args: FinalizeV2Args) -> dict[str, Any]:
    data = finalize(ctx, args)  # 短答案照原来的规则规范化、校验
    return {**data, "answer_text": args.answer_text}


def cite_numbered(ctx: ToolContext, args: Any) -> dict[str, Any]:
    data = cite(ctx, args)
    return {**data, "citation_no": len(ctx.state.citations)}


TOOLS_V2 = {**TOOLS, "finalize": (finalize_v2, FinalizeV2Args),
            "cite": (cite_numbered, TOOLS["cite"][1])}


def schemas_v2() -> list[dict[str, Any]]:
    schemas = copy.deepcopy(TOOL_SCHEMAS)
    for s in schemas:
        fn = s["function"]
        if fn["name"] == "finalize":
            fn["description"] += (" Also submit answer_text: the complete answer shown to "
                                  "the user, "
                                  "with [n] citation markers.")
            fn["parameters"]["properties"]["answer_text"] = {"type": "string", "maxLength": 2000,
                                                             "description": ANSWER_TEXT}
            fn["parameters"]["required"] = ["answers", "answer_text"]
        if fn["name"] == "cite":
            fn["description"] += (" A successful result includes citation_no for [n] markers "
                                  "in answer_text.")
    return schemas


def evaluate(task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    steps = normalize_steps(result.get("steps", []))
    final = result.get("final") or {}
    pkg = package(task["question"], task["answer_format"], final, steps, dedupe=False)
    text = final.get("answer_text") or ""
    return {"text": text, "check": check(text, pkg) if text else None, "pkg": pkg}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--e80-tasks", type=Path, required=True)
    parser.add_argument("--e80-refs", type=Path, required=True)
    parser.add_argument("--model", default="DeepSeek")
    args = parser.parse_args()
    env = Env(args.model)
    tasks = e1_tasks(args.e80_tasks, args.e80_refs)
    schemas = schemas_v2()
    results = parallel(lambda t: run_task(env, t, RESULTS / "e5" / f"{t['task_id']}.json",
                                          env.registry(t, tools=TOOLS_V2, schemas=schemas)), tasks)

    lines = ["# E5 一次交齐（新版 finalize）", ""]
    seconds, with_text, bad_refs, unverified, right = [], 0, 0, 0, 0
    for task, result in zip(tasks, results, strict=True):
        if "error" in result:
            lines += [f"## {task['task_id']}：网关失败 {result['error']}", ""]
            continue
        e = evaluate(task, result)
        seconds.append(result["total_seconds"])
        right += bool(correct(task, result))
        c = e["check"]
        if c:
            with_text += 1
            bad_refs += bool(c["invalid_refs"])
            unverified += bool(c["unverified"])
        short = (result.get("final") or {}).get("submitted") or result.get("violation")
        cited = len(e["pkg"]["citations"])
        checked = (f"引用编号 {c['used_refs']}；非法 {c['invalid_refs']}；"
                   f"未核实数字 {c['unverified']}") if c else "没有回答正文"
        lines += [f"## {task['task_id']}", "", f"问题：{task['question']}", "",
                  f"短答案：{short}；{len(result['steps'])} 步，{result['total_seconds']}s；"
                  f"引用 {cited} 条", "", checked, "", "```text", e["text"].strip(), "```", ""]
    median = statistics.median(seconds)
    lines[1:1] = ["", f"- {len(seconds)} 题跑完；交了回答正文 {with_text} 题；"
                      f"出现非法编号 {bad_refs} 题；有未核实数字 {unverified} 题；"
                      f"短答案答对（有参考答案的题）{right} 题",
                  f"- 总耗时中位数 {median:.1f}s（含检索全过程，和 E1 + E2 的撰写时间对比）"]
    report = "\n".join(lines)
    (RESULTS / "e5_report.md").write_text(report + "\n", encoding="utf-8")
    print("\n".join(lines[:5]))


if __name__ == "__main__":
    main()
