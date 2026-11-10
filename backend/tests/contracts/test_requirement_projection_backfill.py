from __future__ import annotations

"""需求卡投影层的确定性回填契约（plan 2026-11-10-time-range-and-metric-slot-backfill）。

钉住三件事，全部来自一次真机事故（`explicit-region-sales-ranking` 金标 96,318,061.74
vs 实测 326,102,457.72）：

1. 原话里有绝对时间范围时，卡片**不许**留空（否则 SQL 没有时间谓词，把 2023–2026
   四年一起算了）；
2. 原话里点名了注册表口径时，卡片**不许**再问「用哪个指标？」——而且「净销售额」
   不能抢走「销售额」，反过来也一样；
3. 「用哪个指标？」的候选顺序由**原话**决定，不是 `sorted()` 的中文码点序
   （`净` < `销` 曾把用户明说的「销售额」挤出候选）。
"""

import pytest

from app.agent.requirement_parser import time_range_from_query
from app.agent.requirement_slots import match_metric_terms
from app.agent.turn_understanding import assess_reportability, project_card
from app.models.turn_plan import ActiveAnalysisTask, AnalysisGoal

pytestmark = pytest.mark.contracts


# --- D1 · 绝对时间范围抽取（纯函数） ---------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年各区域销售额排名", "2024年"),
        ("2024年7月华东销售额", "2024年7月"),
        ("2024年7月-8月的销售额", "2024年7月-8月"),
        ("2024年7月~9月华东的销售", "2024年7月-9月"),
        ("2024年十一月销售额", "2024年11月"),
        ("看一下 2023 年 11 月的单子", "2023年11月"),
        ("2024年12月-7月（倒序也要归一）", "2024年7月-12月"),
    ],
)
def test_time_range_extracted_from_query(query: str, expected: str) -> None:
    assert time_range_from_query(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "最近卖得咋样",
        "今年销售额",
        "上个月卖了多少",
        "把订单列出来看看",
        "TOP10 2024 的门店",  # 没有「年」→ 不许把裸数字当年份
    ],
)
def test_time_range_absent_or_relative_is_not_invented(query: str) -> None:
    """相对时间词与裸数字**不在这里**换算：返回 None 交回既有链路。"""
    assert time_range_from_query(query) is None


# --- D2 · 口径回填 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("2024年各区域销售额排名", "销售额"),
        ("2024年净销售额多少", "净销售额"),          # 长词优先，不许被「销售额」抢
        ("2024年扣退款销售额多少", "净销售额"),
        ("各区域实收金额", "实收金额"),
    ],
)
def test_single_metric_term_is_backfilled(query: str, expected: str) -> None:
    hits = match_metric_terms(query)
    assert [metric.name for metric in hits] == [expected]


def test_query_without_metric_term_is_not_backfilled() -> None:
    assert match_metric_terms("2024年各区域排名") == []


# --- D3 · 澄清候选顺序 ------------------------------------------------------


def _task(*, metrics: list[str] | None = None, time_range: str | None = None) -> ActiveAnalysisTask:
    return ActiveAnalysisTask(
        revision=1,
        goals=[AnalysisGoal(type="ranking", metric="销售额", dimension="区域")],
        scope=[],
        metrics=list(metrics or []),
        time_range=time_range,
        granularity=None,
        detail_level=None,
    )


KNOWN = {"销售额", "净销售额", "实收金额", "客单价", "订单数", "退款金额"}


def test_metric_options_put_query_hit_first() -> None:
    """用户明说的口径必须是第一个候选（否则夹具盲选就会选错）。"""
    report = assess_reportability(
        _task(), known_metrics=KNOWN, preferred_metrics=["销售额"],
    )
    metric_blocker = next(b for b in report.blockers if b.key == "metric")
    assert metric_blocker.options[0] == "销售额"
    assert len(metric_blocker.options) <= 3


def test_metric_options_without_preference_stay_sorted() -> None:
    """没有原话命中时保持既有的确定性排序（向后兼容，不引入随机顺序）。"""
    report = assess_reportability(_task(), known_metrics=KNOWN)
    metric_blocker = next(b for b in report.blockers if b.key == "metric")
    assert metric_blocker.options == sorted(KNOWN)[:3]


# --- 投影层接线（真机事故形态的回归钉） ------------------------------------


def _card_for(query: str, *, task: ActiveAnalysisTask):
    """复刻 `_requirement_project` 的接线顺序（回填 → 清已答槽位）。"""
    from app.agent.requirement_analysis_graph import (
        _normalize_card_metric,
        _normalize_card_time_range,
    )
    from app.agent.requirement_parser import _drop_satisfied_missing_fields

    report = assess_reportability(task, known_metrics=KNOWN)
    card = project_card(task, reportability=report, summary=query)
    card = _normalize_card_time_range(card, query)
    card = _normalize_card_metric(card, query)
    _drop_satisfied_missing_fields(card)
    if not card.missing_fields:
        card.status = "complete"
    return card


def test_real_incident_shape_is_fully_backfilled() -> None:
    """真机形态：模型给了 `goals[0].metric`，但 `metrics=[]` 且 `time_range=null`。

    修复前：卡片 `time_range=None`、`target_metrics=[]`、且问「用哪个指标？」，
    候选 `净销售额/实收金额/客单价`（用户明说的「销售额」不在其中）。
    """
    card = _card_for("2024年各区域销售额排名", task=_task())

    assert card.time_range == "2024年"
    assert card.target_metrics == ["销售额"]
    assert card.missing_fields == []
    assert card.status == "complete"


def test_model_output_is_respected_when_present() -> None:
    """模型给了值就不覆盖——回填只在留空时发生。"""
    from app.agent.requirement_analysis_graph import (
        _normalize_card_metric,
        _normalize_card_time_range,
    )

    task = _task(metrics=["净销售额"], time_range="2023年")
    card = project_card(task, reportability=assess_reportability(task, known_metrics=KNOWN))

    card = _normalize_card_time_range(card, "2024年各区域销售额排名")
    card = _normalize_card_metric(card, "2024年各区域销售额排名")

    assert card.time_range == "2023年"
    assert card.target_metrics == ["净销售额"]


def test_ambiguous_metric_still_asks() -> None:
    """原话没有点名口径时不猜：仍然保留澄清（回填不许把「没说」变成「说了」）。"""
    card = _card_for("2024年各区域排名", task=_task())

    assert card.time_range == "2024年"
    assert card.target_metrics == []
    assert [mf.key for mf in card.missing_fields] == ["metric"]
    assert card.status == "missing"
