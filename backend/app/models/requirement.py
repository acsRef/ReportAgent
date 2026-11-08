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
    # 2026-11-04（plan 2026-11-04-requirement-slot-contract）：选项的补充说明。
    # 主要承载「口径预览」的数值与差异（`2.61 亿 · 订单金额未扣退款`），也可以是
    # 注册表 note。**契约要点**：label 保持干净、value 保持稳定（前端据此回填
    # selected_value），数值只进 hint——旧前端忽略它也不影响选择逻辑。
    hint: str | None = None


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
    # 2026-11-04：该槽位是「缺失」(missing) 还是「有歧义」(ambiguous)。
    # 歧义项在呈现层优先提问，并带口径预览的数值。
    state: str | None = None


class RequirementAssumption(BaseModel):
    key: str
    text: str
    accepted: bool | None = None
    alternatives: list[RequirementOption] = Field(default_factory=list)


# 2026-11-04（plan 2026-11-04-top-period-detail-intent）：
# 「2024年里销售额最高的那个月」这类问法——先选期、再取该期的数据/明细。
# 之前卡片没有任何字段承载它，SQL 只能做全区间聚合（实测 v2 变成 GROUP BY year,region
# 的一行汇总，用户要的「最高那周/那月」根本没出现）。
class RequirementTopPeriod(BaseModel):
    # both = 两个方向一起要（「销售额最多和最小」），2026-11-05 新增
    direction: Literal["max", "min", "both"]
    # 按哪一期取极值（月 / 周 / 日 / 季 / 年）
    unit: RequirementGranularity
    # 用哪个指标取极值（None = 注册表默认口径）
    metric: str | None = None
    # 2026-11-08（plan top-period-annotate-and-timeout-copy）：「标注」与「只看」是两件事。
    # select   = 只取极值那几期（默认，历史行为）
    # annotate = 保留全区间，另加一列标出哪几期是极值期
    # 用户说「最高的那两个月，标注一下」= annotate——如果只取那两期，「标注」这个词就没意义了。
    purpose: Literal["select", "annotate"] = "select"


class RequirementCard(BaseModel):
    id: str
    version: int = Field(default=1, ge=1)
    status: RequirementStatus
    summary: str
    target_metrics: list[str] = Field(default_factory=list)
    time_range: str | None = None
    granularity: RequirementGranularity | None = None
    detail_level: RequirementDetailLevel | None = None
    # 「最高/最低的那一期」（2026-11-04）。为空 = 普通全区间查询。
    top_period: RequirementTopPeriod | None = None
    scope: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    analysis_methods: list[str] = Field(default_factory=list)
    expected_blocks: list[str] = Field(default_factory=list)
    missing_fields: list[RequirementMissingField] = Field(default_factory=list)
    assumptions: list[RequirementAssumption] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confirmed_at: datetime | None = None
    # 2026-11-04（plan 2026-11-04-requirement-slot-contract）：槽位三态与提问顺序。
    # 默认空 dict/list，旧卡（无该字段）与新前端（不认识就忽略）都兼容；
    # 前端「一次只问一个问题」只依赖这两个字段，不额外推导。
    slot_states: dict[str, str] = Field(default_factory=dict)
    slot_order: list[str] = Field(default_factory=list)

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
