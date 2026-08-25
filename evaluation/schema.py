"""Baseline case 数据集的 Pydantic schema —— 冻结基线 §十四「行为期望」。

设计约束（来自 docs/plans/2026-08-25-baseline-lock-golden-set.md）：
- 可观测即判定；不可观测（memory/retrieval 内部行为）记 deferred，不影响 pass/fail。
- verdict 推导对齐三态语义 SUCCESS / EMPTY / FAILED，不自造第四态。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TurnSpec(BaseModel):
    """一轮对话输入：query + 入口 mode。"""

    query: str = Field(min_length=1)
    mode: Literal["new", "supplement", "adjust"] = "new"


class BehaviorExpectation(BaseModel):
    """行为期望。memory/retrieval 属内部观测 → P13 前一律 deferred。"""

    memory_required: bool | None = None
    memory_types: list[str] = Field(default_factory=list)
    retrieval: bool | None = None
    clarification: bool | None = None


class RequirementExpectation(BaseModel):
    status: Literal["complete", "missing", "locked"] | None = None
    target_metrics_contains: list[str] = Field(default_factory=list)
    time_range_equals: str | None = None
    min_missing_fields: int | None = Field(default=None, ge=0)


class ExecutionExpectation(BaseModel):
    verdict: Literal["SUCCESS", "EMPTY", "FAILED"] | None = None
    sql_nonempty: bool | None = None
    rows_gt: int | None = Field(default=None, ge=0)
    sse_error_code: str | None = None


class ReportExpectation(BaseModel):
    table_present: bool | None = None
    chart_present: bool | None = None
    rows_gt: int | None = Field(default=None, ge=0)


class TurnExpectation(BaseModel):
    """一段轮次期望，四段均可空——空对象 = 本轮无判定项。"""

    requirement: RequirementExpectation | None = None
    execution: ExecutionExpectation | None = None
    report: ReportExpectation | None = None
    behavior: BehaviorExpectation | None = None


class BaselineCase(BaseModel):
    id: str = Field(min_length=3, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    category: str
    description: str = ""
    turns: list[TurnSpec] = Field(min_length=1)
    expectations: list[TurnExpectation] = Field(min_length=1)
    known_gap: bool = False
    requires_fault_injection: bool = False

    @model_validator(mode="after")
    def _check_expectation_len(self) -> "BaselineCase":
        # 长度须为 1（作用于最后一轮）或等于 turns 数。
        if len(self.expectations) not in (1, len(self.turns)):
            raise ValueError(
                f"expectations length must be 1 or len(turns), "
                f"got {len(self.expectations)} vs {len(self.turns)}"
            )
        return self

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        allowed = {
            "explicit_query", "clarification", "multi_turn", "complex_sql",
            "security", "sql_failure", "empty", "report_chart",
            "memory_preference", "chitchat", "schema_retrieval",
            "mcp_failure", "sql_repair",
        }
        if v not in allowed:
            raise ValueError(f"unknown category {v!r}; allowed: {sorted(allowed)}")
        return v
