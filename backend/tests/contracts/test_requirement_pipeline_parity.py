"""两条需求流水线必须产出**同一张卡**（plan 2026-11-10-requirement-pipeline-parity）。

我们实际有两条需求理解路径：

- 路径 A（代码默认，CI 也走这条）：`requirement_parser.parse_requirement`
  —— 模型给结构化字段 → 确定性归一；
- 路径 B（`TURN_UNDERSTANDING=1`）：`TurnPlan` → `reduce_task` → `assess_reportability`
  → `_requirement_project`（`project_card` + 归一）。

同一句原话 + **同一份「模型说了什么」**必须得到同一张执行契约卡，否则同一个缺陷要在两条
路径上各修一遍——2026-11-10 就是这么漏掉一次的（回填只补了 B，A 上「2024年」被算成今年）。

差分探针当时量出 4 类差异，本文件逐条钉住：时间默认方向 / 口径多命中 / 口径缺省 / 明细表示。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.agent.requirement_analysis_graph import _requirement_project
from app.agent.requirement_parser import parse_requirement, resolve_time_range
from app.agent.requirement_slots import match_metric_terms, resolve_metric_slot
from app.agent.turn_understanding import assess_reportability, reduce_task
from app.models.turn_plan import AnalysisGoal, TurnPlan

pytestmark = pytest.mark.contracts

KNOWN = {"销售额", "净销售额", "实收金额", "客单价", "订单数", "销量", "退款金额", "退款率"}


# --- 两条路径的驱动 --------------------------------------------------------


@contextmanager
def _legacy_llm(payload: dict):
    with patch(
        "app.agent.requirement_parser.call_llm",
        side_effect=lambda *a, **kw: json.dumps(payload, ensure_ascii=False),
    ):
        yield


def _legacy_payload(**over) -> dict:
    base = {
        "summary": "", "time_range": None, "target_metrics": [], "scope": [],
        "dimensions": [], "analysis_methods": [], "granularity": None,
        "missing_fields": [], "assumptions": [], "confidence": 0.85,
        "detail_level": None, "clarifications": [],
    }
    base.update(over)
    return base


def _run_legacy(query: str, payload: dict):
    with _legacy_llm(payload):
        return parse_requirement(user_query=query, schema_context=None)


def _run_new(query: str, *, goals):
    plan = TurnPlan(conversation_act="new_task", analysis_goals=list(goals))
    task, _rev = reduce_task(None, plan, user_query=query)
    report = assess_reportability(
        task, known_metrics=KNOWN,
        preferred_metrics=[m.name for m in match_metric_terms(query)],
    )
    return _requirement_project({
        "analysis_task": task, "reportability": report,
        "user_query": query, "trace_id": "",
    })["requirement_card"]


def _sig(card) -> dict:
    """只比**执行契约**字段；summary/confidence 是文案与概率，不在此列。"""
    return {
        "time_range": card.time_range,
        "scope": sorted(card.scope or []),
        "metrics": list(card.target_metrics or []),
        "status": card.status,
        "missing": sorted(mf.key for mf in (card.missing_fields or [])),
        "detail": getattr(card, "detail_level", None),
        "granularity": getattr(card, "granularity", None),
        "dimensions": sorted(card.dimensions or []),
    }


# --- 时间默认方向（独立 fix，有独立用户可见后果） ---------------------------


class TestTimeRangePolicy:
    def test_query_year_wins_over_default(self):
        """原话明写年份时，默认值不许覆盖它（曾经把 2024 算成 2026）。"""
        assert resolve_time_range("2024年各区域销售额排名", None, default_year=True) == "2024年"

    def test_default_only_when_query_has_no_absolute_time(self):
        assert resolve_time_range("各区域销售额排名", None, default_year=True) == "今年"

    def test_no_default_when_disabled(self):
        assert resolve_time_range("各区域销售额排名", None, default_year=False) is None

    def test_existing_value_is_respected(self):
        assert resolve_time_range("2024年各区域销售额", "2023年", default_year=True) == "2023年"

    def test_legacy_defaults_year_from_query_not_today(self):
        """路径 A 真机形态：模型没给时间 → 必须抽原话里的 2024，不是「今年」。"""
        card = _run_legacy(
            "2024年各区域销售额排名",
            _legacy_payload(summary="2024年各区域销售额排名",
                            dimensions=["区域"], analysis_methods=["group_compare"]),
        )
        assert card.time_range == "2024年"


# --- 口径槽位：多命中不是歧义 ------------------------------------------------


class TestMetricSlotPolicy:
    def test_two_distinct_metrics_are_not_ambiguous(self):
        slot = resolve_metric_slot("2024年各品类的销售额和退货金额对比", [], None)
        assert slot.state == "filled"
        assert slot.values == ("销售额", "退款金额")

    def test_single_metric_still_filled(self):
        slot = resolve_metric_slot("2024年各区域销售额", [], None)
        assert slot.state == "filled"
        assert slot.values == ("销售额",)

    def test_nothing_named_stays_ambiguous(self):
        slot = resolve_metric_slot("2024年各区域排名", [], None)
        assert slot.state == "ambiguous"


# --- 同一句话 → 同一张卡 ----------------------------------------------------


PARITY_CASES = [
    pytest.param(
        "2024年各区域销售额排名",
        dict(summary="2024年各区域销售额排名", dimensions=["区域"],
             analysis_methods=["group_compare"]),
        [AnalysisGoal(type="ranking", dimension="区域")],
        id="真机形态-模型只给维度",
    ),
    pytest.param(
        "2024年各区域销售额排名",
        dict(summary="2024年各区域销售额排名", time_range="2024年",
             target_metrics=["销售额"], dimensions=["区域"],
             analysis_methods=["group_compare"]),
        [AnalysisGoal(type="ranking", metric="销售额", dimension="区域")],
        id="模型全给对",
    ),
    pytest.param(
        "帮我看一下",
        dict(summary="帮我看一下"),
        [],
        id="完全模糊",
    ),
    pytest.param(
        "2024年华东地区每个月的销售额趋势",
        dict(summary="2024年华东每个月销售额趋势", time_range="2024年",
             target_metrics=["销售额"], scope=["华东"], dimensions=["时间"],
             analysis_methods=["trend_analysis"], granularity="month"),
        [AnalysisGoal(type="trend", unit="month", metric="销售额")],
        id="单区域-月粒度",
    ),
    pytest.param(
        "2024年各产品类别的销售额和退货金额对比",
        dict(summary="2024年各品类销售额与退货金额对比",
             target_metrics=["销售额", "退款金额"], dimensions=["品类"],
             analysis_methods=["group_compare"]),
        [AnalysisGoal(type="compare", dimension="品类")],
        id="双口径对比",
    ),
]


@pytest.mark.parametrize(("query", "legacy_payload", "goals"), PARITY_CASES)
def test_both_pipelines_produce_the_same_card(query, legacy_payload, goals):
    legacy = _sig(_run_legacy(query, _legacy_payload(**legacy_payload)))
    new = _sig(_run_new(query, goals=goals))
    diff = {field: (legacy[field], new[field]) for field in legacy if legacy[field] != new[field]}
    assert not diff, f"两条流水线对同一句话给出了不同卡片：{diff}"


def test_multi_metric_question_keeps_both_on_both_paths():
    """「销售额和退货金额对比」两条路径都必须给两个口径，且都不许拦下来二选一。"""
    query = "2024年各产品类别的销售额和退货金额对比"
    legacy = _run_legacy(query, _legacy_payload(
        summary=query, dimensions=["品类"], analysis_methods=["group_compare"],
    ))
    new = _run_new(query, goals=[AnalysisGoal(type="compare", dimension="品类")])

    for card in (legacy, new):
        assert card.target_metrics == ["销售额", "退款金额"]
        assert "metric" not in [mf.key for mf in card.missing_fields or []]


def test_vague_query_defaults_metric_on_both_paths():
    """完全没点名口径 → 两条路径都按注册表默认前进（不再问「用哪个指标」）。"""
    query = "帮我看一下"
    legacy = _run_legacy(query, _legacy_payload(summary=query))
    new = _run_new(query, goals=[])

    for card in (legacy, new):
        assert card.target_metrics == ["销售额"]
        assert "metric" not in [mf.key for mf in card.missing_fields or []]
