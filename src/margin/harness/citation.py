"""cite 工具的引用匹配：判断模型给出的 quote 是否真的来自原文。

规则（从严到宽依次尝试，任一级命中即停）：
    1. exact        原文里逐字出现
    2. whitespace   去掉所有空白后出现（PDF 转 Markdown 常多出空格/换行）
    3. normalized   再做全角转半角、大小写统一、忽略标点后出现

被接受还需要同时满足：
    - 在整个 block 里唯一（不唯一就无法确定引的是哪一处）
    - 模型之前通过 read_section / find_in_block 真的“看到过”这段原文（防止凭空编造）
    - 有效长度至少 6 个字符（太短的引用没有证明力）

失败时返回一个 suggested_quote（原文里与 quote 最相近的片段），方便模型照抄修正。
"""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass

MIN_QUOTE_CHARS = 6


@dataclass
class CitationMatch:
    accepted: bool
    error: str | None  # 失败原因：quote_too_short / quote_not_found / quote_ambiguous / ...
    level: str | None  # 命中的匹配级别
    start: int | None  # 在 block 原文中的起止位置
    end: int | None
    matched_text: str | None
    suggested_quote: str | None


def _project(text: str, level: str) -> tuple[str, list[int]]:
    """把文本按匹配级别做变换，同时记录变换后每个字符对应原文的哪个位置。

    有了这张位置映射表，就能在“变换后的文本”里查找，再换算回原文的起止位置。
    """
    chars: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        if level == "normalized":
            for c in unicodedata.normalize("NFKC", char).casefold():
                if unicodedata.category(c)[0] in {"L", "N"}:  # 只保留字母和数字
                    chars.append(c)
                    positions.append(index)
        else:
            chars.append(char)
            positions.append(index)
    return "".join(chars), positions


def _find_all(haystack: str, needle: str) -> list[int]:
    found, start = [], haystack.find(needle)
    while needle and start >= 0:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def _spans(text: str, quote: str, level: str) -> list[tuple[int, int]]:
    """返回 quote 在原文中所有出现位置 [(start, end), ...]。"""
    if level == "exact":
        return [(s, s + len(quote)) for s in _find_all(text, quote)]
    projected_text, positions = _project(text, level)
    projected_quote, _ = _project(quote, level)
    return [
        (positions[s], positions[s + len(projected_quote) - 1] + 1)
        for s in _find_all(projected_text, projected_quote)
    ]


def _suggest(text: str, quote: str) -> str | None:
    """找原文里和 quote 最长的公共片段，作为修正建议。"""
    matcher = difflib.SequenceMatcher(None, text, quote, autojunk=False)
    block = matcher.find_longest_match(0, len(text), 0, len(quote))
    if block.size < MIN_QUOTE_CHARS:
        return None
    # 以公共片段为中心，向两侧扩到和 quote 差不多长，给模型一段完整可抄的原文。
    left = max(0, block.a - block.b)
    return text[left : left + len(quote)]


def match_citation(block_text: str, visible_texts: list[str], quote: str) -> CitationMatch:
    """在 block 原文中匹配 quote，并检查唯一性、可见性和长度。"""
    if len(_project(quote, "normalized")[0]) < MIN_QUOTE_CHARS:
        return CitationMatch(False, "quote_too_short", None, None, None, None, None)

    for level in ("exact", "whitespace", "normalized"):
        spans = _spans(block_text, quote, level)
        if not spans:
            continue
        if len(spans) > 1:
            return CitationMatch(False, "quote_ambiguous", level, None, None, None, None)
        start, end = spans[0]
        matched = block_text[start:end]
        # 可见性：模型看到过的片段里必须也能找到这段原文。
        if not any(_spans(fragment, matched, level) for fragment in visible_texts):
            return CitationMatch(False, "quote_not_visible", level, start, end, matched, matched)
        return CitationMatch(True, None, level, start, end, matched, None)

    return CitationMatch(
        False, "quote_not_found", None, None, None, None, _suggest(block_text, quote)
    )
