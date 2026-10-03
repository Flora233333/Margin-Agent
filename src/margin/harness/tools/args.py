"""十个工具的参数模型（Pydantic）。

模型传来的参数是一段 JSON 字符串，先用这里的模型校验：类型不对、缺字段、超出范围、
多了未知字段（extra="forbid"），都会返回 bad_args 错误让模型自己修正，而不是让程序崩溃。

注意：发给模型看的工具说明在 ../tool_schemas.json（从 RC6-C 原样导出），
这里的约束必须和那份 schema 保持一致。改动任何一边都相当于改了模型接口。
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Keyword = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _nfkc(value: object) -> object:
    """全角转半角并去掉首尾空白；非字符串原样返回，交给类型校验报错。"""
    return unicodedata.normalize("NFKC", value).strip() if isinstance(value, str) else value


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchDocsArgs(Args):
    query: ShortText
    top_k: int = Field(default=8, ge=1, le=8)

    @field_validator("query", mode="before")
    @classmethod
    def _clean_query(cls, value: object) -> object:
        return _nfkc(value)


class SearchInDocumentArgs(Args):
    """query 和 queries 二选一：单个查询，或 2-4 个互不相同的查询（各自独立返回结果）。"""

    doc_id: Id
    query: ShortText | None = None
    queries: list[ShortText] | None = Field(default=None, min_length=2, max_length=4)
    top_k: int = Field(default=5, ge=1, le=8)

    @field_validator("query", mode="before")
    @classmethod
    def _clean_query(cls, value: object) -> object:
        return _nfkc(value)

    @field_validator("queries", mode="before")
    @classmethod
    def _clean_queries(cls, value: object) -> object:
        return [_nfkc(v) for v in value] if isinstance(value, list) else value

    @model_validator(mode="after")
    def _one_of(self) -> SearchInDocumentArgs:
        if (self.query is None) == (self.queries is None):
            raise ValueError("exactly one of query or queries is required")
        if self.queries is not None and len(set(self.queries)) != len(self.queries):
            raise ValueError("queries must be distinct after NFKC normalization and trim")
        return self


class ReadSectionArgs(Args):
    doc_id: Id
    block_id: Id
    row_offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=3000, ge=100, le=6000)


class FindInBlockArgs(Args):
    doc_id: Id
    block_id: Id
    keywords: list[Keyword] = Field(min_length=1, max_length=5)
    match_mode: Literal["any", "all"] = "any"
    before_chars: int = Field(default=200, ge=0, le=500)
    after_chars: int = Field(default=600, ge=0, le=1000)
    max_matches: int = Field(default=3, ge=1, le=5)

    @field_validator("keywords", mode="before")
    @classmethod
    def _clean_keywords(cls, value: object) -> object:
        return [_nfkc(v) for v in value] if isinstance(value, list) else value


class CiteArgs(Args):
    doc_id: Id
    block_id: Id
    quote: str = Field(min_length=2, max_length=500)
    occurrence: int | None = Field(default=None, ge=0)  # schema 里有这个字段，保留以兼容


class ComputeArgs(Args):
    expression: str = Field(min_length=1, max_length=500)
    variables: dict[str, str] = Field(default_factory=dict)
    precision: int = Field(default=2, ge=0, le=8)


class DateCalcArgs(Args):
    base_date: str
    count_from: Literal["same_day", "next_day"]
    duration: int = Field(ge=0, le=100_000)
    unit: Literal["day", "month", "year"]
    calendar: Literal["natural", "working"] = "natural"
    roll: Literal["none", "next_working_day", "previous_working_day"] = "none"


class WriteNoteArgs(Args):
    note: str = Field(min_length=1, max_length=800)


class FinalizeArgs(Args):
    answers: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        min_length=1, max_length=4
    )

    @model_validator(mode="before")
    @classmethod
    def _scalar_to_list(cls, value: object) -> object:
        # 模型有时会写 {"answers": "A"}，兼容成 ["A"]
        if isinstance(value, dict) and isinstance(value.get("answers"), str):
            return {**value, "answers": [value["answers"]]}
        return value


class EscalateArgs(Args):
    reason_code: Literal[
        "no_evidence", "conflicting_evidence", "out_of_scope", "resource_exhausted"
    ]
    detail: str = Field(min_length=1, max_length=300)
