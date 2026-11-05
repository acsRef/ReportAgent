"""Turn Understanding Agent：Reducer / 可报告性 / 卡片投影（plan 2026-11-05-turn-understanding-agent）。

这里全部是**离线确定性**断言（不调模型）：
- 模型产出的 TurnPlan 如何归并到任务状态（继承/改写/清空/新任务）；
- 什么条件下才允许出报告（READY / NEEDS_CLARIFICATION / UNSUPPORTED）；
- 任务如何投影成既有的 RequirementCard 契约（执行层零改动）。

真机效果（强模型理解 + SQL）由 `evaluation/turn_understanding.yaml` 那组用例覆盖，
不在单测里跑。
"""
from __future__ import annotations

import pytest

from app.agent.turn_understanding import (
    assess_reportability,
    project_card,
    reduce_task,
)
from app.models.turn_plan import (
    ActiveAnalysisTask,
    AnalysisGoal,
    TurnPlan,
    Uncertainty,
)

pytestmark = pytest.mark.graphs

KNOWN = {"销售额", "净销售额", "实收金额", "订单数", "销量"}


def _task(**overrides) -> ActiveAnalysisTask:
    base = dict(
        revision=1,
        time_range="2024年",
        scope=["华东"],
        metrics=["销售额"],
        granularity="week",
        goals=[AnalysisGoal(type="trend"), AnalysisGoal(type="anomaly")],
    )
    base.update(overrides)
    return ActiveAnalysisTask(**base)


# --- Reducer：继承 / 改写 / 清空 / 新任务 ------------------------------------


def test_follow_up_inherits_unmentioned_fields() -> None:
    """用户只说「按品类细分」时，时间/范围/口径都不该丢。"""
    plan = TurnPlan(
        conversation_act="follow_up",
        analysis_goals=[AnalysisGoal(type="compare", dimension="品类")],
    )

    task, revision = reduce_task(_task(), plan)

    assert task.time_range == "2024年"
    assert task.scope == ["华东"]
    assert task.metrics == ["销售额"]
    assert task.goals[0].type == "compare"
    assert task.revision == 2
    assert revision.conversation_act == "follow_up"


def test_modify_task_overrides_scope_and_clears_methods() -> None:
    """「不看华东了，看华南」——范围改写、上一轮的目标作废。"""
    plan = TurnPlan(
        conversation_act="modify_task",
        update_fields={"scope": ["华南"]},
        clear_fields=["goals"],
        evidence=["不看华东了，看华南"],
    )

    task, revision = reduce_task(_task(), plan)

    assert task.scope == ["华南"]
    assert task.goals == []
    assert task.time_range == "2024年"          # 没提就继承
    assert revision.changes["scope"] == (["华东"], ["华南"])
    assert revision.evidence == ["不看华东了，看华南"]


def test_new_task_drops_previous_conditions() -> None:
    """全新的任务不该带着上一轮的 2024/华东。"""
    plan = TurnPlan(
        conversation_act="new_task",
        analysis_goals=[AnalysisGoal(type="overview")],
        update_fields={"time_range": "2023年", "metrics": ["退款金额"]},
    )

    task, _ = reduce_task(_task(), plan)

    assert task.time_range == "2023年"
    assert task.metrics == ["退款金额"]
    assert task.scope == []
    assert task.granularity is None
    assert [goal.type for goal in task.goals] == ["overview"]


def test_reducer_ignores_unknown_or_illegal_fields() -> None:
    """模型越权（未知字段 / 非法值）一律忽略，不能让脏数据进状态。"""
    plan = TurnPlan(
        conversation_act="follow_up",
        update_fields={"sql": "DROP TABLE x", "granularity": "hour", "scope": "华南, 西南"},
        clear_fields=["rm -rf"],
    )

    task, _ = reduce_task(_task(), plan)

    assert task.scope == ["华南", "西南"]        # 字符串按逗号拆开
    assert task.granularity == "week"           # 非法粒度被忽略 → 继承
    assert not hasattr(task, "sql")


def test_both_directions_become_two_goals() -> None:
    """「销售额最多和最小」→ 两个 extreme_period 目标（不是一个 direction=both 的猜测）。"""
    plan = TurnPlan(
        conversation_act="follow_up",
        analysis_goals=[
            AnalysisGoal(type="extreme_period", direction="max", unit="month"),
            AnalysisGoal(type="extreme_period", direction="min", unit="month"),
        ],
        clear_fields=["detail_level"],
        update_fields={"granularity": "month"},
    )

    task, _ = reduce_task(_task(), plan)
    card = project_card(task, reportability=assess_reportability(task, known_metrics=KNOWN))

    assert [goal.direction for goal in task.goals] == ["max", "min"]
    assert card.top_period is not None
    assert card.top_period.direction == "both"
    assert card.top_period.unit == "month"
    assert card.granularity == "month"          # 选期单位覆盖上一轮的「按周」
    assert card.analysis_methods == []          # 上一轮的异常检测不再继承
    assert card.detail_level is None


# --- 可报告性 ---------------------------------------------------------------


def test_ready_when_metric_and_goal_present() -> None:
    report = assess_reportability(_task(), known_metrics=KNOWN)

    assert report.verdict == "READY"
    assert report.blockers == []


def test_needs_clarification_when_metric_missing() -> None:
    report = assess_reportability(_task(metrics=[]), known_metrics=KNOWN)

    assert report.verdict == "NEEDS_CLARIFICATION"
    assert [blocker.key for blocker in report.blockers] == ["metric"]


def test_needs_clarification_when_extreme_period_has_no_unit() -> None:
    report = assess_reportability(
        _task(goals=[AnalysisGoal(type="extreme_period", direction="max")]),
        known_metrics=KNOWN,
    )

    assert report.verdict == "NEEDS_CLARIFICATION"
    assert [blocker.key for blocker in report.blockers] == ["granularity"]


def test_unsupported_when_metric_not_in_registry() -> None:
    report = assess_reportability(_task(metrics=["毛利润"]), known_metrics=KNOWN)

    assert report.verdict == "UNSUPPORTED"
    assert "毛利润" in report.notes[0]


def test_blockers_project_into_missing_fields() -> None:
    """NEEDS_CLARIFICATION 的阻塞项要变成卡片的澄清项（复用「一次一问」UI）。"""
    task = _task(metrics=[])
    card = project_card(task, reportability=assess_reportability(task, known_metrics=KNOWN))

    assert card.status == "missing"
    assert [field.key for field in card.missing_fields] == ["metric"]
    assert card.missing_fields[0].options


# --- 与旧路径的语义对齐 -----------------------------------------------------


def test_two_real_turns_reproduce_expected_card() -> None:
    """用户那两轮：第一轮趋势+异常；第二轮「最多和最小」——卡片必须逐字段符合预期。"""
    turn1 = TurnPlan(
        conversation_act="new_task",
        analysis_goals=[AnalysisGoal(type="trend"), AnalysisGoal(type="anomaly")],
        update_fields={
            "time_range": "2024年", "scope": ["华东"], "metrics": ["销售额"], "granularity": "week",
        },
    )
    task1, _ = reduce_task(None, turn1)

    turn2 = TurnPlan(
        conversation_act="follow_up",
        analysis_goals=[
            AnalysisGoal(type="extreme_period", direction="max", unit="month"),
            AnalysisGoal(type="extreme_period", direction="min", unit="month"),
        ],
        clear_fields=["detail_level"],
        update_fields={"granularity": "month"},
        evidence=["销售额最多和最小"],
    )
    task2, revision = reduce_task(task1, turn2)
    card = project_card(task2, reportability=assess_reportability(task2, known_metrics=KNOWN))

    assert card.time_range == "2024年"
    assert card.scope == ["华东"]
    assert card.target_metrics == ["销售额"]
    assert card.granularity == "month"
    assert card.detail_level is None
    assert card.analysis_methods == []
    assert card.top_period is not None and card.top_period.direction == "both"
    assert card.status == "complete"
    assert revision.changes["goals"]  # 目标被替换应有审计


def test_uncertainty_does_not_block_state_reduction() -> None:
    """不确定项只影响「问不问」，不该阻碍状态归并（先归并，再决定问什么）。"""
    plan = TurnPlan(
        conversation_act="follow_up",
        analysis_goals=[AnalysisGoal(type="trend")],
        uncertainties=[Uncertainty(field="granularity", question="按周还是按月？", blocking=True)],
    )

    task, _ = reduce_task(_task(), plan)

    assert task.goals[0].type == "trend"
    assert task.revision == 2
