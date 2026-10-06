"""理解题目（PLAN §5.8 ①）：用执行这道题的同一个模型、一份独立的提示词，从问题里得出三样东西：

    title          左栏和标题栏显示的提纲（不再显示问题原句），例如“广晟控股 2022 年营业收入”
    label          结论旁边的一行说明，例如“广晟控股 · 2022 年营业收入（亿元）”
    answer_format  答案格式（num / pct / date / tf / text），finalize 按它规范化短答案；用户不再手选

提示词在 prompts/understand.txt，不放进 harness/prompts/
（那里是自训模型训练时用的接口，不能随便改）。
实验 E4（2026-10-06，DeepSeek）：格式判断约 89/90，耗时中位数 5.7 秒，
所以 worker 让它和 Harness 并行。

失败（网关报错、超时、模型没按格式输出）不能让这道题失败：
退回“问题前 20 个字 + 文本格式”，记一条日志。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .llm import ChatModel

log = logging.getLogger(__name__)

PROMPT = (Path(__file__).parent / "prompts" / "understand.txt").read_text(encoding="utf-8")
MAX_TOKENS = 2000  # 思考 + 一小段 JSON；E4 里最长的回复远小于这个数


class Understanding(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=80)
    answer_format: Literal["num", "pct", "date", "tf", "text"]


def fallback(question: str) -> Understanding:
    return Understanding(title=question.strip()[:20], answer_format="text")


def understand(llm: ChatModel, question: str) -> Understanding:
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": question}]
    try:
        reply = llm.complete(messages, MAX_TOKENS).content or ""
        # 模型偶尔会把 JSON 包在 ```json 代码块里，只取第一个 { 到最后一个 } 之间
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        return Understanding.model_validate_json(match.group(0) if match else reply)
    except Exception as exc:  # 外部模型调用：任何失败都退回默认值，不影响答题
        log.warning("理解题目失败，使用默认标题和文本格式：%s", type(exc).__name__)
        return fallback(question)
