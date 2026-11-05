"""轮次理解的数据契约（plan 2026-11-05-turn-understanding-agent）。

三层分工（对应 CLAUDE.md 的「Agent 负责理解，代码负责裁决」）：

1. **TurnPlan** —— 强模型**一次**产出的「这一轮想干什么」：对话行为、分析目标、
   要继承/改写/清空的字段、不确定项、以及每条判断的原话证据。
   模型只描述理解，**不直接改任务状态**。
2. **ActiveAnalysisTask** —— 会话级唯一任务状态；由确定性 Reducer 依据 TurnPlan
   归并产生。`RequirementCard` 只是它的投影（执行契约不变）。
3. **TaskRevision** —— 每轮变更审计（哪一轮、改了哪些字段、前后值），
   用于回答「这份报告为什么长这样」。

`ReportabilityVerdict` 由确定性校验产出，模型不参与——避免「模型说可以就可以」。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.requirement import (
    RequirementDetailLevel,
    RequirementGranularity,
)

# --- 分析目标 ---------------------------------------------------------------

GoalType = Literal[
    "overview",         # 总体概况（一个数 / 一组数）
    "trend",            # 随时间的变化
    "compare",          # 维度之间比大小
    "ranking",          # 排名
    "anomaly",          # 异常检测
    "detail_rows",      # 逐条明细
    "extreme_period",   # 某个时间单位上的极值期（最高/最低）
    "distribution",     # 占比 / 分布
]


class AnalysisGoal(BaseModel):
    """一个明确的「要做什么分析」。多个目标 = 用户这一轮想要的多件事。"""

    type: GoalType
    # compare / ranking / distribution 的目标维度（区域 / 品类 / 门店…）
    dimension: str | None = None
    # extreme_period 用：取哪一个方向
    direction: Literal["max", "min"] | None = None
    # extreme_period 用：按哪一期取极值
    unit: RequirementGranularity | None = None
    # 该目标使用的指标（None = 沿用任务的 metrics）
    metric: str | None = None


class Uncertainty(BaseModel):
    """尚未确定的内容。`blocking=True` 表示当前无法完成分析，必须先问。"""

    field: str
    question: str
    options: list[str] = Field(default_factory=list)
    blocking: bool = True


# --- 本轮计划 ---------------------------------------------------------------

ConversationAct = Literal[
    "new_task",             # 与上一轮无关的新任务
    "follow_up",            # 接着上一轮继续看（改角度/加维度）
    "modify_task",          # 明确改写上一轮的某个条件
    "answer_clarification", # 在回答上一轮的澄清问题
    "chitchat",             # 闲聊
]


class TurnPlan(BaseModel):
    conversation_act: ConversationAct
    analysis_goals: list[AnalysisGoal] = Field(default_factory=list)
    # 从上一轮任务继承的字段（仅白名单字段名有效）
    preserve_fields: list[str] = Field(default_factory=list)
    # 本轮改写的字段（Reducer 只接受白名单字段）
    update_fields: dict[str, Any] = Field(default_factory=dict)
    # 本轮作废的字段
    clear_fields: list[str] = Field(default_factory=list)
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    # 每条判断对应的**用户原话**证据（不接受"模型觉得"）
    evidence: list[str] = Field(default_factory=list)


# --- 任务状态 ---------------------------------------------------------------

class ActiveAnalysisTask(BaseModel):
    """会话里「当前在做什么分析」。字段与 RequirementCard 可投影的一一对应。"""

    revision: int = 0
    time_range: str | None = None
    scope: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    granularity: RequirementGranularity | None = None
    detail_level: RequirementDetailLevel | None = None
    goals: list[AnalysisGoal] = Field(default_factory=list)


class TaskRevision(BaseModel):
    """一轮变更的审计记录。"""

    revision: int
    conversation_act: ConversationAct
    changes: dict[str, tuple[Any, Any]] = Field(default_factory=dict)  # field -> (before, after)
    evidence: list[str] = Field(default_factory=list)


# --- 可报告性 ---------------------------------------------------------------

ReportabilityVerdict = Literal["READY", "NEEDS_CLARIFICATION", "UNSUPPORTED"]


class Blocker(BaseModel):
    """可报告性校验的阻塞项；key 与 RequirementMissingField.key 对齐，便于复用澄清 UI。"""

    key: str
    question: str
    options: list[str] = Field(default_factory=list)


class Reportability(BaseModel):
    verdict: ReportabilityVerdict
    blockers: list[Blocker] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# Reducer 允许模型改写的字段白名单（未知字段一律忽略，防止模型越权）
TASK_FIELDS: tuple[str, ...] = (
    "time_range", "scope", "metrics", "granularity", "detail_level", "goals",
)
