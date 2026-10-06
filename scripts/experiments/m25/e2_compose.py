"""E2 撰写回答原型：拿已经跑完的步骤，离线调用“撰写”提示词，把证据写成带 [n] 的回答。

比较两种输入：
    a  笔记 + 引用（带前后文）+ 计算结果 + 提交结果
    b  a 再加每一步的完整思考
看写得好不好（报告里人工读）、会不会编（代码查 [n] 是否合法、数字是否有出处）、多慢。
通过标准（PLAN §5.8）：校验后 [n] 全部合法；抽查 10 篇没有编造的数字；耗时中位数 < 15 秒。

输入：E1 的结果（cache_dir/m25/e1/*.json）+ 历史 run（从本地 API 取，默认 14 16 32）。
结果：cache_dir/m25/e2/<题>.<a|b>.json，以及给人读的 e2_report.md。

用法：
    conda run -n margin --no-capture-output python scripts/experiments/m25/e2_compose.py \
        [--runs 14 16 32]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import httpx
from common import HERE, RESULTS, Env, parallel, plain_chat
from evidence import check, normalize_steps, package

PROMPT = (HERE / "prompts" / "compose_draft.txt").read_text(encoding="utf-8")
API = "http://127.0.0.1:8000"


def render(pkg: dict[str, Any]) -> str:
    """证据包写成给模型看的文字。"""
    final = pkg["final"]
    if final.get("name") == "escalate":
        submitted = f"放弃作答（escalate）：{final.get('reason_code')}，{final.get('detail')}"
    elif final.get("submitted"):
        submitted = "、".join(final["submitted"])
    else:
        submitted = "没有提交答案"
    parts = [f"问题：{pkg['question']}", f"提交结果：{submitted}", "", "引用："]
    if pkg["citations"]:
        for c in pkg["citations"]:
            source = f"{c['doc_id']} · {c['block_id']}"
            parts.append(f"[{c['no']}] {source}：…{c['before']}【{c['match']}】{c['after']}…")
    else:
        parts.append("（无）")
    parts += ["", "计算："]
    parts += [f"{r['expression']} = {r['exact']}（保留后 {r['rounded']}）"
              for r in pkg["computations"]]
    if not pkg["computations"]:
        parts.append("（无）")
    parts += ["", f"工作笔记（模型自己写的中间记录，不是证据）：{pkg['note'] or '（无）'}"]
    if "reasoning" in pkg:
        parts += ["", "检索过程中的思考（按顺序，仅供理解上下文，不是证据）："]
        parts += [f"- {r[:1500]}" for r in pkg["reasoning"]]
    return "\n".join(parts)


def sources(e1_dir: Path, run_ids: list[int]) -> list[dict[str, Any]]:
    """E1 的结果 + 历史 run，统一成 {id, question, answer_format, final, steps}。"""
    items = []
    for path in sorted(e1_dir.glob("*.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        if "steps" in r:
            items.append({"id": path.stem, "question": r["task"]["question"],
                          "answer_format": r["task"]["answer_format"], "final": r["final"],
                          "steps": normalize_steps(r["steps"])})
    for run_id in run_ids:
        run = httpx.get(f"{API}/runs/{run_id}", timeout=10).json()
        attempt = run["attempts"][-1]
        items.append({"id": f"run{run_id}", "question": run["question"],
                      "answer_format": run["answer_format"], "final": attempt.get("final"),
                      "steps": normalize_steps(attempt["steps"])})
    return items


def compose(env: Env, item: dict[str, Any], variant: str) -> dict[str, Any]:
    out = RESULTS / "e2" / f"{item['id']}.{variant}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    pkg = package(item["question"], item["answer_format"], item["final"], item["steps"],
                  with_reasoning=variant == "b")
    reply = plain_chat(env.llm, [{"role": "system", "content": PROMPT},
                                 {"role": "user", "content": render(pkg)}], max_tokens=4000)
    result = {"id": item["id"], "variant": variant, "package": pkg, "reply": reply,
              "check": check(reply["content"], pkg)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[完成] {item['id']}.{variant}：{reply['seconds']}s，"
          f"非法编号 {result['check']['invalid_refs']}，未核实数字 {result['check']['unverified']}",
          flush=True)
    return result


def report(results: list[dict[str, Any]]) -> str:
    lines = ["# E2 撰写回答原型", ""]
    for variant in ("a", "b"):
        rows = [r for r in results if r["variant"] == variant]
        seconds = [r["reply"]["seconds"] for r in rows]
        bad_refs = sum(1 for r in rows if r["check"]["invalid_refs"])
        unverified = sum(1 for r in rows if r["check"]["unverified"])
        tokens = statistics.mean(r["reply"]["usage"].get("prompt_tokens", 0) for r in rows)
        median = statistics.median(seconds)
        lines.append(f"- 输入 {variant}：{len(rows)} 篇，耗时中位数 {median:.1f}s"
                     f"（最长 {max(seconds):.1f}s），出现非法编号 {bad_refs} 篇，"
                     f"有未核实数字 {unverified} 篇，平均输入 {tokens:.0f} token")
    lines.append("")
    for r in sorted(results, key=lambda r: (r["id"], r["variant"])):
        c = r["check"]
        lines += [f"## {r['id']} · 输入 {r['variant']}", "",
                  f"问题：{r['package']['question']}", "",
                  f"提交：{r['package']['final']}", "",
                  f"耗时 {r['reply']['seconds']}s；引用 {c['used_refs']} / "
                  f"共 {c['total_refs']} 条；非法编号 {c['invalid_refs']}；"
                  f"数字 {c['numbers']} 个，未核实 {c['unverified']}", "",
                  "```text", c["text"].strip(), "```", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, nargs="*", default=[14, 16, 32])
    parser.add_argument("--model", default="DeepSeek")
    args = parser.parse_args()
    env = Env(args.model)
    items = sources(RESULTS / "e1", args.runs)
    jobs = [(item, variant) for item in items for variant in ("a", "b")]
    results = parallel(lambda job: compose(env, *job), jobs)
    (RESULTS / "e2_report.md").write_text(report(results), encoding="utf-8")
    print(report(results).split("\n\n")[1])


if __name__ == "__main__":
    main()
