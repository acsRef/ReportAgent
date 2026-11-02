"""业务口径注册表（数据驱动）契约。

背景：2026-11-02 之前「销售额 → fact_orders.order_amount」这类映射写死在
`sql_graph._SCHEMA_TERM_HINTS` 字符串里——换一套 schema 要改代码，加一个指标也要
改代码。现在口径在 `app/knowledge/metrics.yaml`，本文件钉两件事：

1. 解析器能按名字/同义词命中，未注册的指标**不编造**；
2. 需求格式化的输出**随卡片变化**（不是为某一个问题写死的）。
"""
from __future__ import annotations

import pytest

from app.agent.confirmed_execution_graph import _format_confirmed_requirement
from app.knowledge import registry as knowledge
from app.models.requirement import RequirementCard

pytestmark = pytest.mark.contracts


def _card(**overrides) -> RequirementCard:
    base = dict(
        id="d1", version=1, status="complete", summary="测试需求",
        target_metrics=["销售额"], time_range="2024年", granularity="month",
        scope=[], dimensions=[], analysis_methods=[],
        expected_blocks=[], missing_fields=[], assumptions=[],
        confidence=0.9, confirmed_at=None,
    )
    base.update(overrides)
    return RequirementCard(**base)


# --- 解析器 -----------------------------------------------------------------

def test_resolve_metric_by_name_and_synonym():
    for term in ("销售额", "销售", "GMV", "营收"):
        metric = knowledge.resolve_metric(term)
        assert metric is not None, f"{term} 应命中销售额口径"
        assert metric.name == "销售额"
        assert "order_amount" in metric.expr


def test_resolve_apart_metrics_are_distinct():
    assert knowledge.resolve_metric("实收").name == "实收金额"
    assert knowledge.resolve_metric("净销售额").name == "净销售额"
    assert knowledge.resolve_metric("订单数").name == "订单数"


def test_unknown_metric_is_not_invented():
    resolved, unresolved = knowledge.resolve_metrics(["销售额", "火星销售额"])
    assert [m.name for m in resolved] == ["销售额"]
    assert unresolved == ["火星销售额"]


def test_metric_hints_rendered_from_data():
    hints = knowledge.render_metric_hints()
    assert "销售额" in hints and "order_amount" in hints
    # 去重后不应出现「销售额/销售/销售金额/销售额」这种重复别名
    assert "销售额/销售/销售金额/销售额" not in hints


def test_dimension_and_time_bucket_lookup():
    assert knowledge.dimension_column("区域") == "dim_store.region"
    assert knowledge.dimension_column("不存在的维度") is None
    assert knowledge.time_bucket_column("month") == "dim_date.month"


# --- 需求驱动的 SQL 约束（同一套规则，换卡片自动生效） ----------------------

def test_requirement_block_carries_metric_expression_and_scope_rule():
    text = _format_confirmed_requirement(_card(
        scope=["华东"], dimensions=["时间", "区域"],
        analysis_methods=["trend_analysis", "anomaly_detection"],
    ))
    assert "指标表达式" in text and "order_amount" in text
    assert "禁止" in text and "GROUP BY" in text, "范围/分组规则必须写死给模型"
    # 趋势需求：维度在代码层就收窄成时间，不只靠 prompt 提醒
    assert "分析维度 = [时间]" in text
    assert "区域" not in text.split("分析维度 = [时间]")[1].split("\n")[0]
    assert "趋势分析" in text and "只按时间维度分组" in text


def test_requirement_block_adapts_to_a_different_card():
    """换一个完全不同的需求：品类对比 + 订单数——规则与口径自动跟着变。"""
    text = _format_confirmed_requirement(_card(
        summary="2024年各品类订单数对比",
        target_metrics=["订单数"],
        scope=[],
        dimensions=["品类"],
        analysis_methods=["group_compare"],
        granularity=None,
    ))
    assert "COUNT(DISTINCT fact_orders.order_id)" in text
    assert "品类" in text
    assert "趋势分析" not in text, "非趋势需求不应插入趋势约束"


def test_unregistered_metric_is_flagged_not_invented():
    text = _format_confirmed_requirement(_card(
        target_metrics=["火星销售额"], analysis_methods=[],
    ))
    assert "未注册指标" in text
    assert "火星销售额" in text
