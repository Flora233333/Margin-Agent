"""撰写回答（PLAN §5.8 ③）：Harness 结束后，把“已校验的证据”写成一段带 [n] 的回答。

Harness 交出的只是短答案（例如 1204.63），页面要的是设计稿那样的回答：
一句话结论 + 每个事实后面跟着 [n] 的段落 + 可选的“口径说明”。
同一个模型、一份独立的提示词（prompts/compose.txt），一次不带工具的调用，流式推给前端。

输入（实验 E2 的“输入 a”）：问题、提交结果、编号好的引用原文（带前后文）、计算、最新的工作笔记。
不给每一步的思考：E2 里那样输入多 4 倍，回答里的数字反而更容易出错。

模型写完后由代码校验，再写库：
    - 不存在的 [n] 删掉（只留下右侧真有那张卡片的编号）；
    - 回答里的数字要能在问题、引用原文、计算结果、提交的答案里找到，找不到的记为“未核实数字”，
      存下来、页面上轻提示（不删：可能只是换了写法，例如“二〇二二年”）。
工作笔记是模型自己写的，里面的数字没经过 cite 校验，所以给模型看、但不算出处。

失败（网关报错、超时）不能让这道题失败：返回 {"error": ...}，页面退回“短答案 + 依据列表”。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .citations import CitationNumbers
from .harness import Step
from .llm import ChatModel, describe_error

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).parent / "prompts" / "compose.txt").read_text(encoding="utf-8")
MAX_TOKENS = 4000  # 思考 + 300 字以内的回答；E2 里最长的回复远小于这个数
CONTEXT_CHARS = 60  # 引用原文前后各带多少字，让模型知道这个数字是哪一行、哪一列

CITE = re.compile(r"\[(\d+)\]")
NUMBER = re.compile(r"\d[\d,，]*(?:\.\d+)?")


def citations_of(steps: list[Step]) -> list[dict[str, Any]]:
    """通过校验的引用，按 citations.py 的规则编号（和右侧来源卡片是同一套号），带上前后文。"""
    numbers = CitationNumbers()
    citations = []
    for step in steps:
        no = numbers.number(step.tool_name, step.result)
        if no is None:
            continue
        data = step.result["data"]
        match = data["matched_text"]
        before = after = ""
        # 前后文从读过的原文里截：cite 只返回匹配到的那一段
        for text in _read_texts(steps, data["doc_id"], data["block_id"]):
            at = text.find(match)
            if at >= 0:
                before = text[max(0, at - CONTEXT_CHARS):at]
                after = text[at + len(match):at + len(match) + CONTEXT_CHARS]
                break
        citations.append({"no": no, "doc_id": data["doc_id"], "block_id": data["block_id"],
                          "match": match, "before": before, "after": after})
    return citations


def _read_texts(steps: list[Step], doc_id: str, block_id: str) -> list[str]:
    texts = []
    for step in steps:
        data = step.result.get("data") or {}
        if data.get("doc_id") != doc_id or data.get("block_id") != block_id:
            continue
        if step.tool_name == "read_section":
            texts.append(data["text"])
        elif step.tool_name == "find_in_block":
            texts += [m["text"] for m in data["matches"]]
    return texts


def computations_of(steps: list[Step]) -> list[str]:
    """成功的计算，每条写成“算式 = 结果”。"""
    rows = []
    for step in steps:
        if step.tool_name not in ("compute", "date_calc") or not step.result["ok"]:
            continue
        args = json.loads(step.arguments)
        data = step.result["data"]
        if step.tool_name == "compute":
            variables = "，".join(f"{k}={v}" for k, v in (args.get("variables") or {}).items())
            expression = f"{args['expression']}（{variables}）" if variables else args["expression"]
            rows.append(f"{expression} = {data['result_exact']}（保留后 {data['result']}）")
        else:
            start = f"{args['base_date']} 起 {args['duration']} {args['unit']}"
            rows.append(f"{start} = {data['date']}")
    return rows


def latest_note(steps: list[Step]) -> str:
    notes = [s.result["data"]["note"] for s in steps
             if s.tool_name == "write_note" and s.result["ok"]]
    return notes[-1] if notes else ""


def render(question: str, final: dict[str, Any], citations: list[dict[str, Any]],
           computations: list[str], note: str) -> str:
    """证据写成给模型看的文字（和实验 E2 的格式相同）。"""
    if final["name"] == "escalate":
        submitted = f"放弃作答（escalate）：{final['reason_code']}，{final['detail']}"
    else:
        submitted = "、".join(final["submitted"])
    parts = [f"问题：{question}", f"提交结果：{submitted}", "", "引用："]
    parts += [f"[{c['no']}] {c['doc_id']} · {c['block_id']}：…{c['before']}【{c['match']}】"
              f"{c['after']}…" for c in citations] or ["（无）"]
    parts += ["", "计算：", *computations] if computations else ["", "计算：", "（无）"]
    parts += ["", f"工作笔记（模型自己写的中间记录，不是证据）：{note or '（无）'}"]
    return "\n".join(parts)


def _plain(number: str) -> str:
    return number.replace(",", "").replace("，", "")


def check(text: str, question: str, final: dict[str, Any], citations: list[dict[str, Any]],
          computations: list[str]) -> dict[str, Any]:
    """校验回答：删掉不存在的 [n]，找出没有出处的数字。返回存库的 {text, citations, unverified}。"""
    total = len(citations)
    invalid = {n for n in CITE.findall(text) if not 1 <= int(n) <= total}
    if invalid:
        log.warning("回答里有不存在的引用编号 %s，已删除", sorted(invalid, key=int))
    text = CITE.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= total else "", text)

    # 出处：问题、引用原文和前后文、计算、提交的答案。去掉千分位逗号再比较（1,204.63 = 1204.63）
    sources = [question, *final.get("submitted", []), *final.get("raw", []), *computations]
    for c in citations:
        sources += [c["before"], c["match"], c["after"]]
    haystack = _plain(" ".join(sources))
    numbers = {_plain(n) for n in NUMBER.findall(CITE.sub("", text))}
    return {"text": text.strip(), "citations": sorted({int(n) for n in CITE.findall(text)}),
            "unverified": sorted(n for n in numbers if n not in haystack)}


def compose(llm: ChatModel, question: str, final: dict[str, Any], steps: list[Step],
            on_text: Callable[[str], None] | None = None) -> dict[str, Any]:
    """写回答。on_text：正文片段一到就回调（worker 用它把片段推给前端逐字显示）。"""
    citations = citations_of(steps)
    computations = computations_of(steps)
    content = render(question, final, citations, computations, latest_note(steps))
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]

    def forward(kind: str, text: str) -> None:
        if kind == "content" and on_text is not None:  # 思考片段不推：页面只显示回答正文
            on_text(text)

    try:
        reply = llm.complete(messages, MAX_TOKENS, on_delta=forward).content or ""
    except Exception as exc:  # 外部模型调用：任何失败都退回短答案，不影响这道题
        log.warning("撰写回答失败：%s", describe_error(exc))
        return {"error": describe_error(exc)}
    if not reply.strip():
        # 思考型模型偶尔把 max_tokens 全用在思考上，正文是空的
        log.warning("撰写回答失败：模型没有输出正文")
        return {"error": "empty_reply"}
    return check(reply, question, final, citations, computations)
