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
import time
from typing import Any, Callable

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

# 思维链被思考预算截断时追加的标注（UI 上直接可见，所以用中性中文）
REASONING_TRUNCATED_MARKER = "（思考预算用尽，原文在此截断）"


def turn_model() -> str | None:
    """Turn Understanding 专用模型；未配置时回落主模型（None = 用 adapter 默认）。"""
    return (os.getenv("LLM_TURN_MODEL") or "").strip() or None


def fast_retry_timeout() -> float:
    """「不思考」快速重试的超时（默认 30s）。委托 `app.llm.budgets.turn_fast_timeout`。"""
    from app.llm.budgets import turn_fast_timeout

    return turn_fast_timeout()


# 2026-11-08（plan llm-budget-and-accounting D3）：单轮 request budget。
#
# 此前最坏耗时是「思考首轮 90s 超时 + 不思考重试 45s」= 135s，而且第二次也超时的话
# 用户白等一场照样回落。改成：整轮共用一个 deadline（默认 120s），首次超时按剩余时间算，
# 剩余不够就**不重试**——直接回落通用解析，把「卡住」的上界钉死。
#
# 20s 的来由：turn prompt 约 4k token，即便不思考也要十几秒才吐得出 JSON；
# 低于 20s 的重试基本注定失败，只会把卡住再延长一段。
_MIN_RETRY_SECONDS = 20.0


def _remaining(deadline: float) -> float:
    """距本轮 deadline 还剩多少秒（可能为负）。"""
    return deadline - time.monotonic()


def _first_timeout(deadline: float) -> float:
    """首轮超时 = min(单次超时, 本轮剩余)；至少留 1s，避免 0/负数进 httpx。"""
    from app.llm.budgets import turn_timeout

    return max(1.0, min(turn_timeout(), _remaining(deadline)))


def _retry_timeout(deadline: float) -> float | None:
    """重试超时 = min(快速超时, 剩余-5)；剩余不够重试返回 None（不重试）。"""
    remaining = _remaining(deadline) - 5.0
    if remaining < _MIN_RETRY_SECONDS:
        return None
    return max(1.0, min(fast_retry_timeout(), remaining))


def _finish_reply(
    reply,
    meta: dict | None,
    user_query: str = "",
) -> tuple[TurnPlan | None, str]:
    """把一次模型回复收敛成 (TurnPlan, 思维链)——同步/异步两条路共用。

    2026-11-08（plan turn-goal-wording-rules）：目标分类的**代码侧裁决**在这里就做完。
    `reduce_task` 里那次是幂等兜底——评测/离线脚本直接看 `plan.analysis_goals`，
    裁决只写在 reduce 里的话，系统行为是对的、但「计划」本身仍是模型原话的翻译，
    两处对不上（实测：评测 `goals 2/4`，而卡片其实早就被 reduce 纠正过）。
    """
    if reply is None:
        if meta is not None:
            meta["failed"] = True
        return None, ""

    raw, reasoning = reply.content, reply.reasoning
    # 2026-11-08（用户批评：评测从不知道有没有被截断）：
    # 把用量与截断记进 meta，评测/步骤文案都能看见「这次是不是顶到预算了」。
    if meta is not None:
        meta["reasoning_tokens"] = reply.reasoning_tokens
        meta["truncated"] = bool(reply.truncated)
    if reply.truncated and reasoning:
        # 预算用尽：原文停在半句。加一行标注，别让用户以为界面把内容吃了。
        reasoning = f"{reasoning.rstrip()}\n\n{REASONING_TRUNCATED_MARKER}"
        if meta is not None:
            meta["reasoning_truncated"] = True
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        logger.warning("turn understanding returned non-json: %s", str(raw)[:200])
        return None, reasoning
    parsed = _normalize_plan_dict(parsed)
    try:
        plan = TurnPlan.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 - schema 不合法就用回退路径
        logger.warning("turn plan schema invalid: %s | payload=%s", exc, json.dumps(parsed)[:300])
        return None, reasoning
    if plan.analysis_goals:
        refined = _refine_goals_by_wording(
            _veto_goals(plan.analysis_goals, user_query), user_query,
        )
        if refined != plan.analysis_goals:
            plan = plan.model_copy(update={"analysis_goals": refined})
    return plan, reasoning


def _delta_forwarder(on_reasoning: Callable[[str], None] | None) -> Callable[[str, str], None] | None:
    """只转发**思考**增量（回答正文是 JSON，不往界面上怼）。"""
    if on_reasoning is None:
        return None

    def _on_delta(kind: str, text: str) -> None:
        if kind == "reasoning":
            on_reasoning(text)

    return _on_delta


def understand_turn(
    user_query: str,
    *,
    task: ActiveAnalysisTask | None = None,
    history: list[str] | None = None,
    context_block: str = "",
    schema_digest: str = "",
    model: str | None = None,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
    meta: dict | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[TurnPlan | None, str]:
    """调强模型产出 (TurnPlan, 思维链)；失败返回 (None, "") 让调用方回落旧解析。

    - `model`：本次调用用的模型（评测时逐个模型对比用）；None → `LLM_TURN_MODEL` → 主模型；
    - `enable_thinking` / `thinking_budget`：推理模型的思考开关与预算（provider 支持时透传）；
    - `meta`：出参，记录这次走的是哪条路（`retried_without_thinking` / `reasoning_truncated`
      / `failed`），供步骤 detail 如实说明——2026-11-05（plan live-session-issues D1/D2）；
    - `on_reasoning(累计文本)`：流式回调（plan streaming-reasoning）——前端「边跑边看」；
    - 思维链只用于**观测**（trace/调试），不参与任何业务判断。

    失败**不猜**：宁可走旧路径，也不要拿一个半懂的 TurnPlan 去改任务状态。
    但超时/HTTP 失败且默认开着思考时，会**立刻用不思考模式重试一次**（D2）——
    多数情况下能拿回计划，比直接回落到通用解析好。

    **同步版本**（评测/CLI）。async 图节点用 `understand_turn_async`——同步调用会堵住
    事件循环，流式帧只能在结束后一起吐出（实测踩过）。
    """
    from app.llm.budgets import turn_request_budget
    from app.llm.reasoning_client import chat_with_reasoning

    prompt = build_turn_plan_prompt(
        user_query, task=task, history=history,
        context_block=context_block, schema_digest=schema_digest,
    )
    chosen_model = model or turn_model()
    on_delta = _delta_forwarder(on_reasoning)
    deadline = time.monotonic() + turn_request_budget()

    reply = chat_with_reasoning(
        prompt,
        model=chosen_model,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
        timeout=_first_timeout(deadline),
        on_delta=on_delta,
    )
    if reply is None and enable_thinking is not False:
        retry_timeout = _retry_timeout(deadline)
        if retry_timeout is None:
            logger.warning(
                "turn understanding budget exhausted (%.1fs left), skip fast retry",
                _remaining(deadline),
            )
            if meta is not None:
                meta["retry_skipped"] = "budget"
        else:
            logger.warning("turn understanding fell back to fast retry (thinking disabled)")
            reply = chat_with_reasoning(
                prompt,
                model=chosen_model,
                enable_thinking=False,
                timeout=retry_timeout,
            )
            if reply is not None and meta is not None:
                meta["retried_without_thinking"] = True
    return _finish_reply(reply, meta, user_query)


async def understand_turn_async(
    user_query: str,
    *,
    task: ActiveAnalysisTask | None = None,
    history: list[str] | None = None,
    context_block: str = "",
    schema_digest: str = "",
    model: str | None = None,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
    meta: dict | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[TurnPlan | None, str]:
    """`understand_turn` 的 async 版（图节点用；语义与回退策略完全一致）。"""
    from app.llm.budgets import turn_request_budget
    from app.llm.reasoning_client import chat_with_reasoning_async

    prompt = build_turn_plan_prompt(
        user_query, task=task, history=history,
        context_block=context_block, schema_digest=schema_digest,
    )
    chosen_model = model or turn_model()
    on_delta = _delta_forwarder(on_reasoning)
    deadline = time.monotonic() + turn_request_budget()

    reply = await chat_with_reasoning_async(
        prompt,
        model=chosen_model,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
        timeout=_first_timeout(deadline),
        on_delta=on_delta,
    )
    if reply is None and enable_thinking is not False:
        retry_timeout = _retry_timeout(deadline)
        if retry_timeout is None:
            logger.warning(
                "turn understanding budget exhausted (%.1fs left), skip fast retry",
                _remaining(deadline),
            )
            if meta is not None:
                meta["retry_skipped"] = "budget"
        else:
            logger.warning("turn understanding fell back to fast retry (thinking disabled)")
            reply = await chat_with_reasoning_async(
                prompt,
                model=chosen_model,
                enable_thinking=False,
                timeout=retry_timeout,
            )
            if reply is not None and meta is not None:
                meta["retried_without_thinking"] = True
    return _finish_reply(reply, meta, user_query)


def _normalize_plan_dict(parsed: dict) -> dict:
    """宽容处理模型的常见偏差（大小写、单目标不是数组、字段名别名）。"""
    data = dict(parsed)
    goals = data.get("analysis_goals")
    if isinstance(goals, dict):
        data["analysis_goals"] = [goals]
    if not isinstance(data.get("analysis_goals"), list):
        data["analysis_goals"] = []
    # 2026-11-08（真机截图）：模型偶尔会给出两个完全相同的目标（`overview` + `overview`），
    # 下游会当成「要做两遍同样的分析」。按 (type, direction, unit, dimension, metric) 去重。
    deduped: list[dict] = []
    seen_goals: set[tuple] = set()
    for goal in data["analysis_goals"]:
        if not isinstance(goal, dict):
            continue
        key = (
            goal.get("type"), goal.get("direction"), goal.get("unit"),
            goal.get("dimension"), goal.get("metric"),
        )
        if key in seen_goals:
            logger.info("turn plan: drop duplicate goal %s", key)
            continue
        seen_goals.add(key)
        deduped.append(goal)
    data["analysis_goals"] = deduped[:_MAX_GOALS]
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
#
# 2026-11-08（plan turn-goal-wording-rules R2）：原表只有名词（明细/逐条/原始数据…），
# 于是「把刚才最高的那个月**详细列出来**」——最直白的一种明细诉求——被判成「不是明细」，
# 结果模型给的 distribution 目标留下来，用户拿到的是「按区域分布」而不是那一月的逐条。
# 「详细说说/详细讲一下」仍然**不算**（那是要解释，不是要行），所以只加「列出」类动词短语。
_DETAIL_WORDS = (
    "明细", "逐条", "逐笔", "原始数据", "每一笔", "每笔", "详细数据", "数据明细",
    "列出来", "列出", "列一下", "列给我", "逐条列", "都列",
)

# 「按<维度>细分/拆分/分组/对比」= 维度对比，不是趋势（2026-11-08 R1）
_SPLIT_WORDS = ("细分", "拆分", "拆开", "分组", "分类", "分别", "各自", "对比", "比较")


def _dimension_in(text: str) -> str | None:
    """原话里出现的业务维度（复用 requirement_parser 的维度词表，不新造一份）。"""
    from app.agent.requirement_parser import _TOP_PERIOD_DIMENSION_WORDS

    for word in _TOP_PERIOD_DIMENSION_WORDS:
        if word in text:
            return word
    return None


def _refine_goals_by_wording(
    goals: list[AnalysisGoal], user_query: str,
) -> list[AnalysisGoal]:
    """按**原话措辞**纠正模型的目标分类（2026-11-08，plan turn-goal-wording-rules）。

    分工和 `_veto_goals` 一致：模型负责理解，代码负责裁决。区别是这里要**改/补**目标，
    不只是删。三条规则都是确定性的，可单测：

    - R2 明细措辞在场 → 收敛成**单个** `detail_rows`（明细是终态：用户说「列出来」时，
      「按区域分布」这类聚合目标已经过时；`dimension`/`metric` 从被替换的目标继承，
      免得「按品类明细列出来」丢掉品类）；
    - R1 「按<维度>细分/分组/对比」→ `compare`（模型实测读成 `trend`）；
    - R3 原话同时要「最多」和「最少」→ 两个方向必须有（此前靠采样运气，评测那次掉一个）。
    """
    text = str(user_query or "")
    if not text or not goals:
        return goals

    from app.agent.requirement_parser import (
        _TOP_PERIOD_MAX_WORDS,
        _TOP_PERIOD_MIN_WORDS,
    )

    metric = next((goal.metric for goal in goals if goal.metric), None)

    # R2：明细到位 —— 单目标
    if any(word in text for word in _DETAIL_WORDS):
        if not any(goal.type == "detail_rows" for goal in goals):
            logger.info("turn plan: goals refined to detail_rows (explicit listing wording)")
        dimension = next((goal.dimension for goal in goals if goal.dimension), None)
        return [AnalysisGoal(type="detail_rows", dimension=dimension, metric=metric)]

    # R1：维度细分 —— compare
    dimension = _dimension_in(text)
    if dimension and any(word in text for word in _SPLIT_WORDS):
        if not any(goal.type == "compare" for goal in goals):
            logger.info("turn plan: goals refined to compare (dimension = %s)", dimension)
        return [AnalysisGoal(type="compare", dimension=dimension, metric=metric)]

    # R3：极值双向兜底
    has_max = any(word in text for word in _TOP_PERIOD_MAX_WORDS)
    has_min = any(word in text for word in _TOP_PERIOD_MIN_WORDS)
    extremes = [goal for goal in goals if goal.type == "extreme_period"]
    if has_max and has_min and extremes:
        directions = {goal.direction for goal in extremes if goal.direction}
        if not {"max", "min"} <= directions:
            unit = next((goal.unit for goal in extremes if goal.unit), "month")
            unit_metric = extremes[0].metric or metric
            logger.info("turn plan: extreme_period filled to both directions")
            others = [goal for goal in goals if goal.type != "extreme_period"]
            return others + [
                AnalysisGoal(type="extreme_period", direction=d, unit=unit, metric=unit_metric)
                for d in ("max", "min")
            ]

    return goals


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
        base["goals"] = _refine_goals_by_wording(
            _veto_goals(plan.analysis_goals, user_query), user_query,
        )

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


def _registry_has_metric(name: str) -> bool:
    """注册表能不能解析这个指标——**语义**判断，不做字符串集合匹配。

    2026-11-08（用户拍板）：「拿名字跟 set 做精确匹配」这个方案本身就是错的——
    同义词（销售 / GMV / 销售金额）、别名、不同写法都会被判成「未注册」。
    注册表本来负责解析这些，判断也该交给它，而不是自己拼一个字符串集合。
    """
    try:
        from app.knowledge import registry as knowledge

        return knowledge.resolve_metric(str(name or "").strip()) is not None
    except Exception:  # noqa: BLE001 - 知识库不可用时不误杀（宁可放过也不拦用户）
        return True


def assess_reportability(
    task: ActiveAnalysisTask,
    *,
    known_metrics: set[str] | None = None,
    metric_resolver: Callable[[str], bool] | None = None,
) -> Reportability:
    """判断当前任务是否具备出报告的条件。模型不参与。

    `known_metrics`：只用来给「用哪个指标？」的候选列表，**不参与注册判定**；
    `metric_resolver`：注册判定的真实来源（默认问注册表，测试可注入）。
    """
    blockers: list[Blocker] = []
    notes: list[str] = []
    resolver = metric_resolver or _registry_has_metric

    if not task.metrics:
        blockers.append(Blocker(
            key="metric", question="用哪个指标？",
            options=sorted(known_metrics)[:3] if known_metrics else [],
        ))
    else:
        unknown = [m for m in task.metrics if not resolver(m)]
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


# --- 澄清选项的 value 编码（2026-11-08，plan clarification-option-prefixes） ---
#
# 真机事故：`project_card` 把所有 blocker 的选项 value 一律拼成 `metric:<中文>`。
# 于是「想看哪种分析 → 逐条明细」回传成 `metric:逐条明细` → 后端按前缀分派 → **指标被写成
# 「逐条明细」**，而 detail_level 一个字段都没设。用户选了明细，系统却按「总体=一个数」出结果。
#
# 接收侧（`requirement_service.patch_requirement`）的 `intent:` / `time:` / `scope:` 分派
# 早就写好了，缺的只是生成侧发对前缀。这里补上映射，不新造协议。

_PREFIX_BY_FIELD: dict[str, str] = {
    "metric": "metric",
    "analysis_intent": "intent",
    "granularity": "granularity",
    "time_range": "time",
    "scope": "scope",
}

# 中文 label → `_apply_intent` 认得的确定性 code
_INTENT_CODES: dict[str, str] = {
    "总体情况": "overview",
    "按月趋势": "trend",
    "最高与最低": "extreme",
    "逐条明细": "detail",
}

# 中文 label → RequirementGranularity
_GRANULARITY_CODES: dict[str, str] = {
    "按天": "day", "按日": "day",
    "按周": "week", "按星期": "week",
    "按月": "month", "按月份": "month",
    "按季": "quarter", "按季度": "quarter",
    "按年": "year",
}


def _option_value(key: str, label: str) -> str:
    """澄清选项的 value：`<前缀>:<值>`。前缀由 blocker.key 决定——**不是一律 metric**。

    前缀必须和后端的分派表对上（`patch_requirement` 的 Step 1）；发错前缀比不发更糟：
    值会被当成上一层字段写入，卡看着有内容、实际语义全错。
    """
    text = str(label or "").strip()
    prefix = _PREFIX_BY_FIELD.get(str(key or ""), "metric")
    if prefix == "intent":
        return f"intent:{_INTENT_CODES.get(text, text or 'overview')}"
    if prefix == "granularity":
        return f"granularity:{_GRANULARITY_CODES.get(text, text or 'month')}"
    return f"{prefix}:{text}"


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
    # 模型给的口径名不可信（实测给了「销售」这种半截词）→ 归一到注册表业务名
    if task.metrics:
        from app.agent.requirement_parser import _display_metric

        task.metrics = [_display_metric(str(m)) for m in task.metrics]
        metric = task.metrics[0]
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
            options=[
                RequirementOption(label=opt, value=_option_value(blocker.key, opt))
                for opt in blocker.options
            ],
        ))
    # 数据能力不支持（指标/实体不在注册表/Schema）也要拦住：不能装成一张「可直接确认」的卡
    if reportability is not None and reportability.verdict == "UNSUPPORTED":
        missing.append(RequirementMissingField(
            key="unsupported",
            label="、".join(reportability.notes) or "当前数据不支持这个分析",
            kind="single",
            options=[],
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
