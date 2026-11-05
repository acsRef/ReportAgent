"""Turn Understanding Agent + 确定性 Reducer + 可报告性裁决（plan 2026-11-05-turn-understanding-agent）。

分工（用户拍板）：
- **模型负责理解**：`understand_turn()` 一次产出 `TurnPlan`（强模型，`LLM_TURN_MODEL`）；
- **代码负责裁决**：`reduce_task()` 归并任务状态、`assess_reportability()` 判断能不能出报告、
  `project_card()` 投影成既有 `RequirementCard` 契约（SQL/Report/前端零改动）。

为什么不把「理解」和「槽位抽取」串成两次 LLM：两个模型可能各自理解出不同的需求，
最后谁覆盖谁说不清。这里只有**一次**模型理解，剩下全是确定性代码。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.agent.prompts.turn_plan_prompts import build_turn_plan_prompt
from app.models.requirement import (
    RequirementAssumption,
    RequirementCard,
    RequirementMissingField,
    RequirementOption,
    RequirementTopPeriod,
)
from app.models.turn_plan import (
    TASK_FIELDS,
    ActiveAnalysisTask,
    AnalysisGoal,
    Blocker,
    Reportability,
    TaskRevision,
    TurnPlan,
)
from app.utils.text import safe_json_parse

logger = logging.getLogger(__name__)

# 极值目标最多支持两个方向（「最多和最小」）；再多就属于新的交互形态，先不猜。
_MAX_GOALS = 6


def turn_model() -> str | None:
    """Turn Understanding 专用模型；未配置时回落主模型（None = 用 adapter 默认）。"""
    return (os.getenv("LLM_TURN_MODEL") or "").strip() or None


def understand_turn(
    user_query: str,
    *,
    task: ActiveAnalysisTask | None = None,
    history: list[str] | None = None,
    schema_digest: str = "",
) -> TurnPlan | None:
    """调强模型产出 TurnPlan；失败返回 None（调用方回落到既有 requirement_parse 路径）。

    失败**不猜**：宁可走旧路径，也不要拿一个半懂的 TurnPlan 去改任务状态。
    """
    from app.llm import call_llm

    prompt = build_turn_plan_prompt(
        user_query, task=task, history=history, schema_digest=schema_digest,
    )
    kwargs: dict[str, Any] = {"max_tokens": 1200}
    model = turn_model()
    if model:
        kwargs["model"] = model
    try:
        raw = call_llm(prompt, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 理解失败回落旧路径
        logger.warning("turn understanding llm failed: %s", exc)
        return None
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        logger.warning("turn understanding returned non-json: %s", str(raw)[:200])
        return None
    parsed = _normalize_plan_dict(parsed)
    try:
        return TurnPlan.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 - schema 不合法就用回退路径
        logger.warning("turn plan schema invalid: %s | payload=%s", exc, json.dumps(parsed)[:300])
        return None


def _normalize_plan_dict(parsed: dict) -> dict:
    """宽容处理模型的常见偏差（大小写、单目标不是数组、字段名别名）。"""
    data = dict(parsed)
    goals = data.get("analysis_goals")
    if isinstance(goals, dict):
        data["analysis_goals"] = [goals]
    if not isinstance(data.get("analysis_goals"), list):
        data["analysis_goals"] = []
    data["analysis_goals"] = data["analysis_goals"][:_MAX_GOALS]
    for key in ("preserve_fields", "clear_fields", "evidence"):
        value = data.get(key)
        if isinstance(value, str):
            data[key] = [value]
        elif not isinstance(value, list):
            data[key] = []
    if not isinstance(data.get("update_fields"), dict):
        data["update_fields"] = {}
    if not isinstance(data.get("uncertainties"), list):
        data["uncertainties"] = []
    act = str(data.get("conversation_act") or "").strip().lower()
    aliases = {
        "new": "new_task", "followup": "follow_up", "follow-up": "follow_up",
        "modify": "modify_task", "answer": "answer_clarification",
        "answer_question": "answer_clarification", "chat": "chitchat",
    }
    data["conversation_act"] = aliases.get(act, act)
    return data


# --- 确定性 Reducer ---------------------------------------------------------


_VALID_GRANULARITY = {"day", "week", "month", "quarter", "year"}
_VALID_DETAIL = {"aggregate", "detail"}
# 只有用户原话里出现这些词，才允许「逐条明细」这个目标存在（模型猜的不算）
_DETAIL_WORDS = ("明细", "逐条", "逐笔", "原始数据", "每一笔", "每笔", "详细数据", "数据明细")


def _veto_goals(goals: list[AnalysisGoal], user_query: str) -> list[AnalysisGoal]:
    """代码侧裁决：拦掉模型过度解读产生的目标。

    实测（2026-11-05）：用户说「销售额最多和最小的**具体情况**是什么」，模型把它读成
    「要逐条明细」。代码侧必须以原话为准：没有明细词就不允许 `detail_rows` 目标。
    """
    text = str(user_query or "")
    has_detail_word = any(word in text for word in _DETAIL_WORDS)
    kept: list[AnalysisGoal] = []
    for goal in goals:
        if goal.type == "detail_rows" and not has_detail_word:
            logger.info("turn plan: drop detail_rows goal (no explicit detail wording)")
            continue
        kept.append(goal)
    return kept


def _apply_update(task: dict, field: str, value: Any) -> None:
    """白名单字段的类型收敛（模型给的值不可信，这里做最后一道校验）。"""
    if field == "scope":
        if isinstance(value, str):
            task["scope"] = [
                part.strip() for part in value.replace("，", ",").split(",") if part.strip()
            ]
        elif isinstance(value, list):
            task["scope"] = [str(item).strip() for item in value if str(item).strip()]
    elif field == "metrics":
        if isinstance(value, str):
            task["metrics"] = [value.strip()]
        elif isinstance(value, list):
            task["metrics"] = [str(item).strip() for item in value if str(item).strip()]
    elif field == "goals":
        goals: list[AnalysisGoal] = []
        for raw in (value if isinstance(value, list) else []):
            if isinstance(raw, dict):
                try:
                    goals.append(AnalysisGoal.model_validate(raw))
                except Exception:  # noqa: BLE001 - 单个目标不合法就丢掉
                    continue
        if goals:
            task["goals"] = goals
    elif field == "granularity":
        # 非法粒度（模型写 "hour"）直接忽略 → 沿用 prior，别让整个归并失败
        text = str(value or "").strip().lower()
        if text in _VALID_GRANULARITY:
            task["granularity"] = text
    elif field == "detail_level":
        text = str(value or "").strip().lower()
        if text in _VALID_DETAIL:
            task["detail_level"] = text
    elif field == "time_range":
        task["time_range"] = str(value).strip() if value not in ("", None) else None


def reduce_task(
    prior: ActiveAnalysisTask | None, plan: TurnPlan, *, user_query: str = "",
) -> tuple[ActiveAnalysisTask, TaskRevision]:
    """把 TurnPlan 归并到当前任务上（纯函数，可单测）。

    规则：
    - `new_task`：丢弃 prior，从 plan 重建（历史条件不带到新任务）；
    - 其余 act：以 prior 为基，顺序是 **clear → update → preserve**，
      没出现在三个集合里的字段沿用 prior（缺省=继承）。

    `user_query`：用于**代码侧的最后一道裁决**——例如模型把「具体情况」读成
    「逐条明细」时，用户原话里没有「明细/逐条/原始数据/每一笔」就丢该目标。
    这是「模型负责理解、代码负责裁决」的落点，不是再改 prompt。
    """
    base: dict[str, Any] = (
        {} if prior is None or plan.conversation_act == "new_task"
        else prior.model_dump(mode="json")
    )
    before = dict(base)
    base.pop("revision", None)

    for field in plan.clear_fields:
        if field in TASK_FIELDS:
            base[field] = [] if field in ("scope", "metrics", "goals") else None
    for field, value in plan.update_fields.items():
        if field in TASK_FIELDS:
            _apply_update(base, field, value)
    # preserve_fields 只是「明确要求保留」；缺省继承语义已由 base 保证，
    # 这里不做事，但保留参数语义供审计。
    if plan.analysis_goals:
        # 这一轮明确给了目标 → 目标以这一轮为准（避免旧目标残留）
        base["goals"] = _veto_goals(plan.analysis_goals, user_query)

    for field in TASK_FIELDS:
        base.setdefault(field, [] if field in ("scope", "metrics", "goals") else None)

    task = ActiveAnalysisTask(
        revision=(prior.revision if prior else 0) + 1,
        **{key: base.get(key) for key in TASK_FIELDS},
    )
    after = task.model_dump(mode="json")
    changes = {
        field: (before.get(field), after.get(field))
        for field in TASK_FIELDS
        if before.get(field) != after.get(field)
    }
    return task, TaskRevision(
        revision=task.revision,
        conversation_act=plan.conversation_act,
        changes=changes,
        evidence=list(plan.evidence),
    )


# --- 可报告性（确定性裁决） -------------------------------------------------


def assess_reportability(
    task: ActiveAnalysisTask,
    *,
    known_metrics: set[str] | None = None,
) -> Reportability:
    """判断当前任务是否具备出报告的条件。模型不参与。"""
    blockers: list[Blocker] = []
    notes: list[str] = []

    if not task.metrics:
        blockers.append(Blocker(
            key="metric", question="用哪个指标？",
            options=sorted(known_metrics)[:3] if known_metrics else [],
        ))
    elif known_metrics is not None:
        unknown = [m for m in task.metrics if m not in known_metrics]
        if unknown:
            return Reportability(
                verdict="UNSUPPORTED",
                notes=[f"指标未注册：{'、'.join(unknown)}"],
            )

    if not task.goals:
        blockers.append(Blocker(
            key="analysis_intent",
            question="想看哪种分析？",
            options=["总体情况", "按月趋势", "最高与最低", "逐条明细"],
        ))

    for goal in task.goals:
        if goal.type == "extreme_period" and not goal.unit:
            blockers.append(Blocker(
                key="granularity", question="按什么时间单位找极值？",
                options=["按月", "按周", "按天"],
            ))
            break

    if not task.time_range:
        notes.append("时间范围未限定（默认按全部数据理解）")

    if blockers:
        return Reportability(verdict="NEEDS_CLARIFICATION", blockers=blockers, notes=notes)
    return Reportability(verdict="READY", notes=notes)


# --- 投影成 RequirementCard（执行契约不变） --------------------------------

_GOAL_TO_METHOD = {
    "trend": "trend_analysis",
    "compare": "group_compare",
    "ranking": "group_compare",
    "distribution": "group_compare",
    "anomaly": "anomaly_detection",
}

_GOAL_TO_DIMENSION = {
    "trend": "时间",
}


def project_card(
    task: ActiveAnalysisTask,
    *,
    reportability: Reportability | None = None,
    summary: str = "",
    confidence: float = 0.8,
) -> RequirementCard:
    """ActiveAnalysisTask → RequirementCard（唯一执行契约，字段语义保持不变）。"""
    methods: list[str] = []
    dimensions: list[str] = []
    top_period: RequirementTopPeriod | None = None
    detail_level = task.detail_level
    extreme_goals = [g for g in task.goals if g.type == "extreme_period"]

    for goal in task.goals:
        method = _GOAL_TO_METHOD.get(goal.type)
        if method and method not in methods:
            methods.append(method)
        dimension = goal.dimension or _GOAL_TO_DIMENSION.get(goal.type)
        if dimension and dimension not in dimensions:
            dimensions.append(dimension)
        if goal.type == "detail_rows":
            detail_level = "detail"

    metric = (task.metrics or [None])[0]
    if extreme_goals:
        directions = {g.direction for g in extreme_goals if g.direction}
        units = [g.unit for g in extreme_goals if g.unit]
        direction = "both" if {"max", "min"} <= directions else (
            "min" if "min" in directions else "max"
        )
        top_period = RequirementTopPeriod(
            direction=direction,
            unit=(units[0] if units else "month"),
            metric=metric,
        )
        # 选期本身就决定了粒度：不要沿用上一轮趋势的按周
        task.granularity = (units[0] if units else "month")
    elif task.granularity is None:
        # 趋势/异常必须有时间粒度；模型没给就按既有确定性规则补（异常看跨度、趋势默认月），
        # 不能让「模型忘了写」变成 SQL 按年分组只出一行。
        goal_types = {goal.type for goal in task.goals}
        if "anomaly" in goal_types:
            from app.agent.requirement_parser import _default_anomaly_granularity

            task.granularity = _default_anomaly_granularity(task.time_range)
        elif "trend" in goal_types:
            task.granularity = "month"

    missing: list[RequirementMissingField] = []
    for blocker in (reportability.blockers if reportability else []):
        missing.append(RequirementMissingField(
            key=blocker.key,
            label=blocker.question,
            kind="single",
            options=[RequirementOption(label=opt, value=f"metric:{opt}") for opt in blocker.options],
        ))

    return RequirementCard(
        id=f"draft-{os.getpid()}-{task.revision}",
        version=max(1, task.revision),
        status="missing" if missing else "complete",
        summary=summary or _summarize(task),
        target_metrics=list(task.metrics),
        time_range=task.time_range,
        granularity=task.granularity,
        detail_level=detail_level,
        scope=list(task.scope),
        dimensions=dimensions,
        analysis_methods=methods,
        expected_blocks=[],
        missing_fields=missing,
        assumptions=[],
        confidence=confidence,
        confirmed_at=None,
        top_period=top_period,
    )


def _summarize(task: ActiveAnalysisTask) -> str:
    parts = [task.time_range or "", "、".join(task.scope), "、".join(task.metrics)]
    goals = "、".join(goal.type for goal in task.goals)
    text = " ".join(part for part in parts if part)
    return f"{text}（{goals}）" if goals else text


def load_task_blocking(session_id: str):
    """同步读最近任务（graph 节点是 sync 函数；DB 不可用时返回 None）。"""
    from app.infra.db.analysis_task_repository import load_task_blocking as _load

    return _load(session_id)


def save_task_blocking(session_id: str, user_id: int, task, revision) -> None:
    """同步写一条任务审计（append-only；失败只告警）。"""
    from app.infra.db.analysis_task_repository import save_task_blocking as _save

    _save(session_id, user_id, task, revision)


__all__ = [
    "turn_model",
    "understand_turn",
    "reduce_task",
    "assess_reportability",
    "project_card",
    "RequirementAssumption",
]
