"""目标分类的代码侧裁决（plan 2026-11-08-turn-goal-wording-rules）。

基线评测 `goals 2/4`，根因是「`_veto_goals` 只删不补」：明细词表只收名词，
把最直白的「详细**列出来**」误判成「不是明细」；「按产品**细分**」被模型读成 trend；
「最多和最小」靠采样运气。三条确定性规则分别对应，本文件钉住它们与**不误伤**的边界。
"""
from __future__ import annotations

import pytest

from app.agent.turn_understanding import (
    _DETAIL_WORDS,
    _refine_goals_by_wording,
    _veto_goals,
    reduce_task,
)
from app.models.turn_plan import ActiveAnalysisTask, AnalysisGoal, TurnPlan

pytestmark = pytest.mark.graphs


def _goal(type_: str, **kwargs) -> AnalysisGoal:
    return AnalysisGoal(type=type_, **kwargs)  # type: ignore[arg-type]


def _types(goals: list[AnalysisGoal]) -> set[str]:
    return {goal.type for goal in goals}


# ── R2 明细到位：收敛成 detail_rows ────────────────────────────────────────


class TestDetailWording:
    @pytest.mark.parametrize("query", [
        "把刚才最高的那个月详细列出来",
        "按品类明细列出来",
        "把订单列一下",
        "这几个月的数据列给我",
        "给我逐条列出来",
    ])
    def test_明细措辞收敛成单目标(self, query: str) -> None:
        goals = [_goal("trend")]
        refined = _refine_goals_by_wording(goals, query)

        assert _types(refined) == {"detail_rows"}

    def test_继承维度与口径(self) -> None:
        """「按品类明细列出来」不能把品类弄丢——维度从被替换的目标继承。"""
        goals = [_goal("distribution", dimension="品类", metric="销售额")]
        refined = _refine_goals_by_wording(goals, "按品类明细列出来")

        assert len(refined) == 1
        assert refined[0].type == "detail_rows"
        assert refined[0].dimension == "品类"
        assert refined[0].metric == "销售额"

    @pytest.mark.parametrize("query", [
        "详细说说这份报告",     # 要解释，不要行
        "详细讲一下",
        "销售额最多和最小的具体情况是什么",  # t2：不能变明细
        "帮我分析一下销量",
    ])
    def test_非明细措辞不改(self, query: str) -> None:
        goals = [_goal("trend")]
        assert _refine_goals_by_wording(goals, query) == goals

    def test_veto_也认新的明细词(self) -> None:
        """`_veto_goals` 和 `_refine` 必须用同一张词表，否则「列出来」会被先删掉。"""
        assert "列出来" in _DETAIL_WORDS
        goals = [_goal("detail_rows")]
        assert _veto_goals(goals, "把那个月详细列出来") == goals


# ── R1 维度细分 → compare ─────────────────────────────────────────────────


class TestSplitWording:
    @pytest.mark.parametrize("query,dimension", [
        ("再按产品细分", "产品"),
        ("按品类拆开看看", "品类"),
        ("分区域对比一下", "区域"),
        ("再按门店分组", "门店"),
        ("各渠道各自表现", "渠道"),
    ])
    def test_细分收敛成_compare(self, query: str, dimension: str) -> None:
        refined = _refine_goals_by_wording([_goal("trend")], query)

        assert _types(refined) == {"compare"}
        assert refined[0].dimension == dimension

    @pytest.mark.parametrize("query", [
        "2024年销售趋势",
        "看下变化",
        "按月份看",
    ])
    def test_不是细分就不改(self, query: str) -> None:
        goals = [_goal("trend", dimension="时间")]
        assert _refine_goals_by_wording(goals, query) == goals

    def test_有细分词但没维度词不改(self) -> None:
        goals = [_goal("trend")]
        assert _refine_goals_by_wording(goals, "帮我细分一下") == goals


# ── R3 极值双向兜底 ───────────────────────────────────────────────────────


class TestExtremeBoth:
    def test_只给一个方向时补齐(self) -> None:
        goals = [_goal("extreme_period", direction="max", unit="month", metric="销售额")]
        refined = _refine_goals_by_wording(goals, "销售额最多和最小的具体情况是什么")

        directions = {g.direction for g in refined if g.type == "extreme_period"}
        assert directions == {"max", "min"}
        assert {g.unit for g in refined if g.type == "extreme_period"} == {"month"}

    def test_已经两个方向就不动(self) -> None:
        goals = [
            _goal("extreme_period", direction="max", unit="month"),
            _goal("extreme_period", direction="min", unit="month"),
        ]
        assert _refine_goals_by_wording(goals, "销售额最多和最小") == goals

    def test_只提一个方向就不补(self) -> None:
        goals = [_goal("extreme_period", direction="max", unit="month")]
        assert _refine_goals_by_wording(goals, "销售额最高的那个月") == goals

    def test_保留其它目标(self) -> None:
        goals = [
            _goal("extreme_period", direction="max", unit="month"),
            _goal("anomaly"),
        ]
        refined = _refine_goals_by_wording(goals, "销售额最多和最小，顺便看有没有异常")

        assert "anomaly" in _types(refined)
        assert {g.direction for g in refined if g.type == "extreme_period"} == {"max", "min"}


# ── 优先级与整体归并 ──────────────────────────────────────────────────────


class TestPriority:
    def test_明细优先于细分(self) -> None:
        """「按品类细分并列出明细」：明细是终态，不该只剩 compare。"""
        refined = _refine_goals_by_wording([_goal("trend")], "按品类细分并列出明细")
        assert _types(refined) == {"detail_rows"}
    def test_reduce_task_里生效(self) -> None:
        prior = ActiveAnalysisTask(
            time_range="2024年", scope=["华东"], metrics=["销售额"], granularity="month",
            goals=[_goal("trend")], revision=1,
        )
        plan = TurnPlan(
            conversation_act="follow_up",
            analysis_goals=[_goal("trend", dimension="产品")],
            preserve_fields=["time_range", "scope", "metrics"],
        )

        task, revision = reduce_task(prior, plan, user_query="再按产品细分")

        assert {g.type for g in task.goals} == {"compare"}
        assert "goals" in revision.changes
        # 继承的东西不能被顺手改掉
        assert task.time_range == "2024年" and task.scope == ["华东"]


class TestDuplicateGoals:
    """模型偶尔给两个一模一样的 target（真机截图：`overview` + `overview`）。"""

    def test_完全相同的目标去重(self) -> None:
        from app.agent.turn_understanding import _normalize_plan_dict

        data = _normalize_plan_dict({
            "conversation_act": "follow_up",
            "analysis_goals": [
                {"type": "overview", "metric": "销售额"},
                {"type": "overview", "metric": "销售额"},
            ],
        })

        assert data["analysis_goals"] == [{"type": "overview", "metric": "销售额"}]

    def test_类型相同但参数不同的保留(self) -> None:
        from app.agent.turn_understanding import _normalize_plan_dict

        data = _normalize_plan_dict({
            "conversation_act": "follow_up",
            "analysis_goals": [
                {"type": "extreme_period", "direction": "max", "unit": "month"},
                {"type": "extreme_period", "direction": "min", "unit": "month"},
                {"type": "trend"},
            ],
        })

        assert len(data["analysis_goals"]) == 3

    def test_非字典目标被丢掉(self) -> None:
        from app.agent.turn_understanding import _normalize_plan_dict

        data = _normalize_plan_dict({
            "conversation_act": "new_task",
            "analysis_goals": ["trend", {"type": "trend"}],
        })

        assert data["analysis_goals"] == [{"type": "trend"}]


class TestDecisionHappensBeforeReturn:
    """裁决必须在 `understand_turn` 返回前生效。

    只写在 `reduce_task` 里的话，系统行为是对的、但 `plan.analysis_goals` 仍是模型原话的
    翻译——评测和离线脚本直接看计划，就会得出「模型不懂」的错误结论（实测 goals 2/4）。
    """

    @pytest.mark.parametrize("query,expected", [
        ("再按产品细分", "compare"),
        ("把刚才最高的那个月详细列出来", "detail_rows"),
    ])
    def test_计划返回时已裁决(
        self, monkeypatch: pytest.MonkeyPatch, query: str, expected: str,
    ) -> None:
        import json as _json

        from app.agent import turn_understanding as tu
        from app.llm.reasoning_client import ReasoningReply

        model_output = _json.dumps({
            "conversation_act": "follow_up",
            "analysis_goals": [{"type": "trend", "dimension": "产品", "metric": "销售额"}],
            "preserve_fields": ["time_range"],
            "update_fields": {},
            "clear_fields": [],
            "uncertainties": [],
            "evidence": [query],
        }, ensure_ascii=False)

        def fake(prompt: str, **kwargs: object) -> ReasoningReply:
            return ReasoningReply(content=model_output, reasoning="想了", model="m")

        monkeypatch.setattr("app.llm.reasoning_client.chat_with_reasoning", fake)
        plan, _reasoning = tu.understand_turn(query)

        assert plan is not None
        assert {goal.type for goal in plan.analysis_goals} == {expected}
