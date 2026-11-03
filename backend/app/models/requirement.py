from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


RequirementStatus = Literal["missing", "complete", "locked"]
# 时间粒度：用户要求「按月/每周/按季度」时的时间分组单位。
# 之所以要独立字段：time_range 只表达范围（"2024年"），不表达粒度；
# 之前粒度只作为 missing_field 存在，用户明说「每个月」时无处存放 →
# 需求卡丢信息 → SQL 只按年分组（2026-10-28 实测 1 行，应为 12 行）。
RequirementGranularity = Literal["day", "week", "month", "quarter", "year"]
# 展示粒度：aggregate = 聚合后的汇总（默认）；detail = 逐条明细。
# 为什么独立字段：2026-11-03 实测「给我这两个月的明细数据」——需求卡里没有任何
# 字段承载「不要聚合」，SQL 仍然 GROUP BY month 出 2 行，用户要的明细根本没出现。
RequirementDetailLevel = Literal["aggregate", "detail"]
RequirementFieldKey = str
"""缺失/澄清项的 key。

2026-11-03 起不再是固定枚举：澄清项由**思考模型**根据 MCP 返回的表结构现场生成
（分析角度、口径、时间范围、维度…），写死 5 个 key 会让「销售情况」这种模糊需求
无处表达「你到底想看哪种情况」。key 只用于前后端往返标识，语义由选项的 value 前缀
（`intent:` / `metric:` / `time:` / `scope:`）承载。
"""
RequirementFieldKind = Literal["single", "multiple"]

SelectedValue = str | list[str]


class RequirementOption(BaseModel):
    label: str
    value: str


class RequirementMissingField(BaseModel):
    key: RequirementFieldKey
    # 面向用户的问句（2026-11-03 起由模型现场生成，如「你想看哪种『情况』？」）
    label: str
    kind: RequirementFieldKind = "single"
    options: list[RequirementOption] = Field(default_factory=list)
    # The user's selection for this field. For `kind=single`, this is a
    # single string (or None if unselected). For `kind=multiple`, a
    # list of strings (possibly empty). The service layer translates
    # selections into the card's structured fields (time_range, scope,
    # etc.) and removes this MissingField from the persisted card
    # when filled.
    selected_value: SelectedValue | None = None


class RequirementAssumption(BaseModel):
    key: str
    text: str
    accepted: bool | None = None
    alternatives: list[RequirementOption] = Field(default_factory=list)


class RequirementCard(BaseModel):
    id: str
    version: int = Field(default=1, ge=1)
    status: RequirementStatus
    summary: str
    target_metrics: list[str] = Field(default_factory=list)
    time_range: str | None = None
    granularity: RequirementGranularity | None = None
    detail_level: RequirementDetailLevel | None = None
    scope: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    analysis_methods: list[str] = Field(default_factory=list)
    expected_blocks: list[str] = Field(default_factory=list)
    missing_fields: list[RequirementMissingField] = Field(default_factory=list)
    assumptions: list[RequirementAssumption] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status_consistency(self) -> RequirementCard:
        # NOTE: status/form-state consistency used to be enforced here.
        # We relaxed it because PATCH bodies carry whatever `status` the
        # server issued at analysis time, even after the user filled the
        # form. The hard invariants are now enforced by
        # `service.requirement_service.patch_requirement`, which
        # normalizes the card before persisting it. This validator now
        # only enforces the `locked` invariants, which are still strict
        # because locked cards come from server-side execution, never
        # from the client.
        if self.status == "locked" and self.confirmed_at is None:
            raise ValueError("locked requirement requires confirmed_at")
        if self.status != "locked" and self.confirmed_at is not None:
            raise ValueError("only locked requirement can contain confirmed_at")
        return self
