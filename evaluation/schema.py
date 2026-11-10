"""Baseline case 数据集的 Pydantic schema —— 冻结基线 §十四「行为期望」。

设计约束（来自 docs/plans/2026-08-25-baseline-lock-golden-set.md）：
- 可观测即判定；不可观测（memory/retrieval 内部行为）记 deferred，不影响 pass/fail。
- verdict 推导对齐三态语义 SUCCESS / EMPTY / FAILED，不自造第四态。

P14 升级：TurnExpectation 加 extra='allow'，让 dynamic dim key（memory / retrieval /
sql / tool_selection / repair / frontend / e2e）走 Pydantic roundtrip 不丢失。
Typed 字段（requirement/execution/report/behavior）保持 strict 校验。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TurnSpec(BaseModel):
    """一轮对话输入：query + 入口 mode。"""

    query: str = Field(min_length=1)
    mode: Literal["new", "supplement", "adjust"] = "new"


class FaultSpec(BaseModel):
    """故障注入规格 —— 把「这条 case 靠什么 header 造故障」写进数据集。

    两个 seam 都在 backend 侧 fail-closed（必须 `REPORTAGENT_E2E=1`，否则
    header 恒被忽略，生产零行为变化）：

    - `fault`    → `X-E2E-Fault: kind=<kind>;mode=<mode>`（`reliability/fault_inject.py`）
    - `mcp_down` → `X-E2E-McpDown: on`（`reliability/mcp_down.py`）

    `on` 决定头挂到哪个请求：`confirm` 测执行层，`chat` 测需求层检索，
    `both` 两层一起降级。
    """

    model_config = ConfigDict(extra="forbid")

    seam: Literal["fault", "mcp_down"] = "fault"
    kind: Literal["object_not_found", "permission"] = "permission"
    mode: Literal["once", "persistent"] = "persistent"
    on: Literal["chat", "confirm", "both"] = "both"

    def header(self) -> dict[str, str]:
        """该规格对应的唯一请求头。"""
        if self.seam == "mcp_down":
            return {"X-E2E-McpDown": "on"}
        return {"X-E2E-Fault": f"kind={self.kind};mode={self.mode}"}

    def headers_for(self, request: Literal["chat", "confirm"]) -> dict[str, str]:
        """该请求是否需要带头（`on` 不覆盖此请求时返回空 dict）。"""
        if self.on not in ("both", request):
            return {}
        return self.header()


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


class GoldenResult(BaseModel):
    """金标结果（Execution Accuracy 的唯一判据来源，plan 2026-11-10）。

    两种写法，`sql` 优先：

    - `sql`：**推荐**。评测时用只读执行这条 SQL 得到金标，跟着种子数据走，
      不会因为 seed 更新而写死一堆数字；
    - `rows`/`columns`：金标行内联（适合结果很小、或不依赖种子数据的场景）。
    """

    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    float_tol: float = Field(default=0.01, ge=0)
    match_columns: bool = False


class ExecutionExpectation(BaseModel):
    verdict: Literal["SUCCESS", "EMPTY", "FAILED"] | None = None
    sql_nonempty: bool | None = None
    rows_gt: int | None = Field(default=None, ge=0)
    sse_error_code: str | None = None
    # 有金标时，runner 会额外算 execution accuracy（结果对不对），与「跑没跑通」分开
    golden: GoldenResult | None = None


class ReportExpectation(BaseModel):
    table_present: bool | None = None
    chart_present: bool | None = None
    rows_gt: int | None = Field(default=None, ge=0)


class TurnExpectation(BaseModel):
    """一段轮次期望：4 段 typed（strict）+ 任意 dynamic dim key（allow）。

    P14 P0 闭环：dynamic dim key（如 memory/retrieval/sql/tool_selection/repair/
    frontend/e2e）必须穿过 model_validate → model_dump 不丢失，让 runner.run_case
    的 check_turn Phase 2 dispatcher 拿得到。
    """

    model_config = ConfigDict(extra="allow")  # P14: 允许 dynamic dim key roundtrip

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
    # 2026-11-02：故障用例必须给出注入规格，否则 runner 只能 skip——
    # 「占位 skip」在报告里看起来像“跑过了”，实际错误路径零覆盖。
    fault: FaultSpec | None = None

    @model_validator(mode="after")
    def _fault_spec_marks_case(self) -> "BaselineCase":
        if self.fault is not None:
            self.requires_fault_injection = True
        return self

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
