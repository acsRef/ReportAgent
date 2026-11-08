"""澄清选项的 value 前缀（plan 2026-11-08-clarification-option-prefixes）。

真机事故：`project_card` 把所有 blocker 的选项 value 一律拼成 `metric:<中文>`，
于是「想看哪种分析 → 逐条明细」回传成 `metric:逐条明细` →
**指标被写成「逐条明细」**，`detail_level` 一个字段没设。
用户选了明细，系统却按「总体=一个数」出结果（实测 SQL = `SELECT SUM(...)`，1 行）。
"""
from __future__ import annotations

import pytest

from app.agent.turn_understanding import _option_value, project_card
from app.models.turn_plan import ActiveAnalysisTask, Blocker, Reportability
from app.services.requirement_service import _apply_intent, _normalize_granularity

pytestmark = pytest.mark.contracts


# ── 1. 编码：前缀由 blocker.key 决定 ──────────────────────────────────────


class TestOptionValue:
    @pytest.mark.parametrize(("key", "label", "expected"), [
        ("analysis_intent", "总体情况", "intent:overview"),
        ("analysis_intent", "按月趋势", "intent:trend"),
        ("analysis_intent", "最高与最低", "intent:extreme"),
        ("analysis_intent", "逐条明细", "intent:detail"),
        ("metric", "销售额", "metric:销售额"),
        ("granularity", "按月", "granularity:month"),
        ("granularity", "按周", "granularity:week"),
        ("granularity", "按天", "granularity:day"),
        ("time_range", "2024年", "time:2024年"),
        ("scope", "华东", "scope:华东"),
    ])
    def test_前缀按_key_分派(self, key: str, label: str, expected: str) -> None:
        assert _option_value(key, label) == expected

    def test_分析角度绝不能编码成_metric(self) -> None:
        """这是真机事故的那一行：analysis_intent 的选项曾经一律 `metric:`。"""
        value = _option_value("analysis_intent", "逐条明细")
        assert not value.startswith("metric:")

    def test_未知_key_回落_metric(self) -> None:
        assert _option_value("something_new", "销售额") == "metric:销售额"

    def test_label_已是英文时不重复翻译(self) -> None:
        assert _option_value("granularity", "month") == "granularity:month"
        assert _option_value("analysis_intent", "detail") == "intent:detail"


# ── 2. 产卡：选项真的带上正确前缀 ─────────────────────────────────────────


class TestProjectCardOptions:
    def _card(self):
        reportability = Reportability(
            verdict="NEEDS_CLARIFICATION",
            blockers=[
                Blocker(
                    key="analysis_intent",
                    question="想看哪种分析？",
                    options=["总体情况", "按月趋势", "最高与最低", "逐条明细"],
                ),
                Blocker(key="metric", question="用哪个指标？", options=["销售额", "净销售额"]),
            ],
        )
        task = ActiveAnalysisTask(revision=1)  # type: ignore[call-arg]
        return project_card(task, reportability=reportability, summary="把订单列出来看看")

    def test_分析角度的选项是_intent_前缀(self) -> None:
        field = next(f for f in self._card().missing_fields if f.key == "analysis_intent")
        values = [option.value for option in field.options]

        assert values == ["intent:overview", "intent:trend", "intent:extreme", "intent:detail"]
        assert all(not value.startswith("metric:") for value in values)

    def test_指标的选项仍是_metric_前缀(self) -> None:
        field = next(f for f in self._card().missing_fields if f.key == "metric")
        assert [option.value for option in field.options] == ["metric:销售额", "metric:净销售额"]

    def test_中文_label_没变(self) -> None:
        field = next(f for f in self._card().missing_fields if f.key == "analysis_intent")
        assert [option.label for option in field.options] == [
            "总体情况", "按月趋势", "最高与最低", "逐条明细",
        ]


# ── 3. 接收侧：前缀被正确翻译回卡字段 ─────────────────────────────────────


class TestApplyIntent:
    def test_逐条明细落_detail_level(self) -> None:
        card: dict = {"target_metrics": ["销售额"], "dimensions": ["时间"]}
        _apply_intent(card, "detail")

        assert card["detail_level"] == "detail"
        assert card["analysis_methods"] == []
        assert card["target_metrics"] == ["销售额"]   # 指标不许被污染

    def test_按月趋势落粒度(self) -> None:
        card: dict = {}
        _apply_intent(card, "trend")

        assert card["granularity"] == "month"
        assert "trend_analysis" in card["analysis_methods"]

    def test_最高与最低落_top_period(self) -> None:
        card: dict = {}
        _apply_intent(card, "extreme")

        assert card["top_period"]["direction"] == "both"
        assert card["top_period"]["unit"] == "month"

    def test_总体情况不落方法(self) -> None:
        card: dict = {"granularity": "week", "analysis_methods": ["trend_analysis"]}
        _apply_intent(card, "overview")

        assert card["analysis_methods"] == []
        assert card["detail_level"] is None


class TestUnknownMetricGuard:
    """假指标不能进卡（真机事故的另一半：「总体情况」被当成指标，SQL 照跑出总额）。"""

    @pytest.mark.parametrize("name", ["销售额", "净销售额", "订单数"])
    def test_注册表里的口径放行(self, name: str) -> None:
        from app.services.requirement_service import _is_known_metric

        assert _is_known_metric(name) is True

    @pytest.mark.parametrize("name", ["总体情况", "逐条明细", "按月趋势", "", "随便写的"])
    def test_非注册表口径拦下(self, name: str) -> None:
        from app.services.requirement_service import _is_known_metric

        assert _is_known_metric(name) is False


class TestGranularityNormalization:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("按月", "month"), ("按周", "week"), ("按天", "day"), ("按日", "day"),
        ("按季度", "quarter"), ("按年", "year"),
        ("month", "month"), ("week", "week"), ("quarter", "quarter"),
    ])
    def test_中英文都归一(self, raw: str, expected: str) -> None:
        assert _normalize_granularity(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "随便写的", "daily"])
    def test_认不出返回_None_而不是写坏卡(self, raw: str) -> None:
        assert _normalize_granularity(raw) is None
