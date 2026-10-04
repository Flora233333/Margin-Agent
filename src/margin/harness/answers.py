"""答案格式规范化：把模型 finalize 提交的答案整理成统一格式。

为什么需要它：同一个答案模型可能写成 "正确" / "对" / "A"，或 "12.3%" / "12.30 %"。
finalize 工具先用这里的规则规范化；评测时也用同一套规则比较，保证口径一致。

answer_format（题目的答案类型）取值：
    tf     判断题，规范成 A（正确）/ B（错误）
    mcq    单选题，一个字母
    multi  多选题，字母去重后按字母序拼接，如 "ACD"
    num    数值，保留两位小数
    pct    百分比，保留两位小数并带 %
    date   日期，统一成 "2024年3月5日"
    rank   排序，如 "A>B>C"
    text   自由文本，只做无损清理
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# 每种格式规范化之后必须满足的样子；不满足说明模型提交的答案有歧义。
FORMAT_RE: dict[str, re.Pattern[str]] = {
    "multi": re.compile(r"^[A-D]{1,4}$"),
    "mcq": re.compile(r"^[A-D]$"),
    "tf": re.compile(r"^[AB]$"),
    "num": re.compile(r"^-?\d+(?:\.\d+)?$"),
    "pct": re.compile(r"^-?\d+(?:\.\d+)?%$"),
    "date": re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日$"),
    "rank": re.compile(r"^[^<>=]+(?:>[^<>=]+)+$"),
    "text": re.compile(r"^.+$"),
}

# 合法的数字写法。先校验再去掉千分位，防止 "1,23" 被悄悄变成 123。
_NUMERIC_INPUT = re.compile(
    r"^[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)


def to_halfwidth(text: str) -> str:
    """全角转半角（NFKC），例如 "１２％" -> "12%"。"""
    return unicodedata.normalize("NFKC", text).replace("／", "/")


def round_half_up(value: Decimal, digits: int) -> Decimal:
    """四舍五入（不是 Python 默认的银行家舍入），并把 -0.00 变成 0.00。"""
    rounded = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return abs(rounded) if rounded.is_zero() else rounded


def normalize_one(answer: str, kind: str) -> str:
    """规范化单个答案分量。"""
    a = re.sub(r"\s+", "", to_halfwidth(str(answer)).strip())
    a = a.replace("，", ",").replace("＞", ">")
    if kind == "tf":
        value = a.casefold()
        if value in {"正确", "对", "true", "是", "a"}:
            return "A"
        if value in {"错误", "错", "false", "否", "b"}:
            return "B"
        return value.upper()
    if kind in {"num", "pct"}:
        numeric = a[:-1] if kind == "pct" and a.endswith("%") else a
        try:
            number = f"{round_half_up(Decimal(numeric.replace(',', '')), 2):.2f}"
        except InvalidOperation:
            return a
        return number + "%" if kind == "pct" else number
    if kind == "date":
        m = re.match(r"^(\d{4})(?:年|[-/.])(\d{1,2})(?:月|[-/.])(\d{1,2})(?:日)?$", a)
        return f"{int(m[1])}年{int(m[2])}月{int(m[3])}日" if m else a
    if kind == "rank":
        return re.sub(r"\s*>\s*", ">", to_halfwidth(str(answer)).strip())
    if kind == "text":
        # 文本只做无损清理：分号属于正文，不拆分。
        value = unicodedata.normalize("NFKC", str(answer)).strip()
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\s*[,，]\s*", ",", value)
        return re.sub(r"\s*[;；]\s*", ";", value)
    return a


def normalize_answers(values: list[str], kind: str, max_components: int = 4) -> list[str]:
    """把 finalize.answers 规范化成答案分量列表；有歧义时抛 ValueError。

    例：
        normalize_answers(["A、C"], "multi")       -> ["AC"]
        normalize_answers(["12.345;8"], "num")      -> ["12.35", "8.00"]
        normalize_answers(["对"], "tf")             -> ["A"]
    """
    if kind not in FORMAT_RE:
        raise ValueError(f"unsupported answer format: {kind}")

    # 选择题：把所有字母合并，去掉分隔符，去重排序。
    if kind in {"mcq", "multi"}:
        letters = re.sub(r"[、,，;；/|\s]", "", "".join(to_halfwidth(v) for v in values)).upper()
        if not re.fullmatch(r"[A-D]+", letters):
            raise ValueError(f"answer cannot be normalized unambiguously as {kind}")
        letters = "".join(sorted(set(letters)))
        if kind == "mcq" and len(letters) != 1:
            raise ValueError("mcq requires exactly one option")
        return [letters]

    # 数值/日期/排序：一个字符串里可能用分号、顿号、换行写了多个分量，拆开。
    components: list[str] = []
    for item in values:
        if kind in {"num", "pct", "date", "rank"}:
            item = to_halfwidth(item).replace("\r\n", "\n").replace("\r", "\n")
            separators = r"[;、\n]"
            if kind == "pct":
                # "12%,8%" 里百分号后面的逗号是分隔符；"1,234.5%" 里的逗号是千分位。
                separators += r"|(?<=%)[,，]\s*(?=[+-]?(?:\d|\.))"
            parts = re.split(separators, item)
            if any(not part.strip() for part in parts):
                raise ValueError("empty answer component")
            components.extend(part.strip() for part in parts)
        else:
            components.append(item)
    if not components or len(components) > max_components:
        raise ValueError(f"{kind} requires between one and {max_components} components")

    if kind in {"num", "pct"}:
        for item in components:
            raw = to_halfwidth(item).strip()
            if kind == "pct" and raw.endswith("%"):
                raw = raw[:-1].rstrip()
            if not _NUMERIC_INPUT.fullmatch(raw):
                raise ValueError(f"invalid numeric syntax for {kind}")

    normalized = [normalize_one(item, kind) for item in components]
    if any(not FORMAT_RE[kind].fullmatch(v) for v in normalized):
        raise ValueError(f"answer cannot be normalized unambiguously as {kind}")
    if kind == "date":
        for v in normalized:
            date(*map(int, re.findall(r"\d+", v)))  # 非法日期（如 2月30日）会抛 ValueError
    return normalized

