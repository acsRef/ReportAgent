"""「最高的那一期 + 明细」这类问法的确定性落地（plan 2026-11-04-top-period-detail-intent）。

用户原话：「我只要2024年份中最多月份的那个月数据详细图」。实测这一轮之前会发生三件事：
①`detail_level` 停在 aggregate（「详细图」不在词表里）②上一轮的 `anomaly_detection` 被继承
③模型反问「你想看哪种视角」。本文件把三处都钉住，并钉住执行层的两段式 SQL 契约。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs


# --- 原话识别 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("我只要2024年份中最多月份的那个月数据详细图", {"direction": "max", "unit": "month"}),
        ("那最多那一周为什么销售额这么多呢？帮我分析一下", {"direction": "max", "unit": "week"}),
        ("2024年销售额最低的月份是哪个月", {"direction": "min", "unit": "month"}),
        ("看下最高那天的流水", {"direction": "max", "unit": "day"}),
        # 没有「期」的说法 → 不认（只要一个数，不是选期）
        ("2024年销售额最高的数值是多少", None),
        ("帮我分析一下销量", None),
    ],
)
def test_top_period_from_query(query: str, expected: dict | None) -> None:
    from app.agent.requirement_parser import _top_period_from_query

    assert _top_period_from_query(query) == expected


@pytest.mark.parametrize(
    "query",
    ["我只要2024年份中最多月份的那个月数据详细图", "给我这两个月的明细数据", "只要详细数据"],
)
def test_detail_words_detected(query: str) -> None:
    from app.agent.requirement_parser import _normalize_detail_level

    assert _normalize_detail_level(query) == "detail"


def test_bare_xiangxi_is_not_detail() -> None:
    """「详细讲一下」是叙述诉求，不是要明细行——不能把 adjust 变成明细查询。"""
    from app.agent.requirement_parser import _normalize_detail_level

    assert _normalize_detail_level("详细讲一下这份报告") is None


# --- 需求卡片：模型说错了也要按用户原话纠正 ----------------------------------


def _patch_llm(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    import json

    import app.agent.requirement_parser as parser_mod
    import app.llm as llm_mod

    text = json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(parser_mod, "call_llm", lambda *a, **k: text)
    monkeypatch.setattr(llm_mod, "call_llm", lambda *a, **k: text)


def _model_payload_like_real_failure() -> dict:
    """复刻真库 draft 4388 的模型输出（这是被用户判为「没听懂」的那一轮）。"""
    return {
        "summary": "分析2024年华东销售额最高的月份数据",
        "target_metrics": ["销售额"],
        "time_range": "2024年",
        "granularity": "month",
        "scope": ["华东"],
        "dimensions": ["时间", "区域"],
        "analysis_methods": ["anomaly_detection"],
        "confidence": 0.85,
        "missing_fields": [
            {
                "key": "analysis_intent",
                "label": "你想看2024年销售情况的哪种视角？",
                "kind": "single",
                "options": [
                    {"label": "全年总览", "value": "intent:overview"},
                    {"label": "按月趋势", "value": "intent:trend"},
                    {"label": "区域对比", "value": "intent:compare_region"},
                ],
            }
        ],
        "assumptions": [],
    }


def test_real_failure_query_becomes_top_period_detail(monkeypatch) -> None:
    _patch_llm(monkeypatch, _model_payload_like_real_failure())

    from app.agent.requirement_parser import parse_requirement

    card = parse_requirement(
        user_query="我只要2024年份中最多月份的那个月数据详细图",
        schema_context=None,
    )

    # ① 明细：用户说了「数据详细图」
    assert card.detail_level == "detail"
    # ② 选期：最高 + 月 + 口径（中文名，界面与 prompt 都读它）
    assert card.top_period is not None
    assert card.top_period.direction == "max"
    assert card.top_period.unit == "month"
    assert card.top_period.metric == "销售额"
    # ③ 不许再问「哪种视角」——用户已经说清楚了
    keys = {mf.key for mf in card.missing_fields}
    assert "analysis_intent" not in keys
    assert card.slot_states.get("analysis_intent") != "missing"
    # ④ 上一轮的异常检测不许继承到这一轮
    assert card.analysis_methods == []
    # ⑤ 选期要按月聚合，粒度必须落下来（否则 SQL 按年分组）
    assert card.granularity == "month"


def test_top_period_without_detail_stays_aggregate(monkeypatch) -> None:
    payload = _model_payload_like_real_failure()
    payload["summary"] = "分析2024年华东销售额最高的月份"
    _patch_llm(monkeypatch, payload)

    from app.agent.requirement_parser import parse_requirement

    card = parse_requirement(
        user_query="那最多那一周为什么销售额这么多呢",
        schema_context=None,
    )

    assert card.top_period is not None
    assert card.top_period.direction == "max"
    assert card.top_period.unit == "week"
    assert card.detail_level is None
    assert "analysis_intent" not in {mf.key for mf in card.missing_fields}


# --- 槽位：有选期就不再算「分析角度缺失」 ------------------------------------


def test_slot_states_do_not_ask_intent_for_top_period() -> None:
    from app.agent.requirement_slots import slot_states
    from app.models.requirement import RequirementCard, RequirementTopPeriod

    card = RequirementCard(
        id="d1",
        status="complete",
        summary="2024年销售额最高的月份",
        target_metrics=["销售额"],
        time_range="2024年",
        granularity="month",
        top_period=RequirementTopPeriod(direction="max", unit="month", metric="销售额"),
    )

    states = slot_states(card)

    assert "analysis_intent" not in states
    assert states["granularity"] == "filled"


# --- 执行层：两段式指令与 SQL 契约 ------------------------------------------


def test_top_period_hint_gives_two_step_structure() -> None:
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    from app.models.requirement import RequirementCard, RequirementTopPeriod

    card = RequirementCard(
        id="d1",
        status="complete",
        summary="2024年华东销售额最高的月份的明细",
        target_metrics=["销售额"],
        time_range="2024年",
        granularity="month",
        scope=["华东"],
        detail_level="detail",
        top_period=RequirementTopPeriod(direction="max", unit="month", metric="销售额"),
    )

    text = _format_confirmed_requirement(card) or ""

    assert "最高的月" in text
    assert "LIMIT 1" in text
    assert "dim_date.month" in text
    assert "逐条明细" in text
    assert "明细模式" in text  # 明细块仍在，且在两段式之后


def test_cte_strip_keeps_only_final_query() -> None:
    from app.agent.sql_graph import _strip_cte_blocks

    sql = (
        "WITH top_period AS (\n"
        "  SELECT dim_date.month AS 期 FROM fact_orders GROUP BY dim_date.month "
        "ORDER BY SUM(fact_orders.order_amount) DESC LIMIT 1\n"
        ")\n"
        "SELECT dim_date.full_date AS 日期, fact_orders.order_amount AS 订单金额\n"
        "FROM fact_orders JOIN top_period ON dim_date.month = top_period.期"
    )
    stripped = _strip_cte_blocks(sql)

    assert stripped.lstrip().upper().startswith("SELECT")
    assert "group by" not in stripped.lower()
    assert "sum(" not in stripped.lower()


def test_detail_contract_allows_aggregate_inside_cte() -> None:
    from app.agent.sql_graph import _detail_contract_violation

    sql = (
        "WITH top_period AS (SELECT dim_date.month AS 期 FROM fact_orders "
        "GROUP BY dim_date.month ORDER BY SUM(fact_orders.order_amount) DESC LIMIT 1) "
        "SELECT dim_date.full_date AS 日期, dim_store.region AS 区域, "
        "fact_orders.order_amount AS 订单金额 FROM fact_orders "
        "JOIN top_period ON dim_date.month = top_period.期 LIMIT 200"
    )

    assert _detail_contract_violation(sql) is not None      # 不含 CTE 例外 → 仍判违规
    assert _detail_contract_violation(sql, allow_cte_aggregate=True) is None


def test_top_period_contract_requires_period_selection() -> None:
    from app.agent.sql_graph import _top_period_contract_violation

    flat = (
        "SELECT dim_date.year AS 年份, dim_store.region AS 区域, "
        "SUM(fact_orders.order_amount) AS 销售额 FROM fact_orders "
        "GROUP BY dim_date.year, dim_store.region"
    )
    two_step = (
        "WITH top_period AS (SELECT dim_date.month AS 期 FROM fact_orders "
        "GROUP BY dim_date.month ORDER BY SUM(fact_orders.order_amount) DESC LIMIT 1) "
        "SELECT * FROM fact_orders"
    )

    assert _top_period_contract_violation(flat) is not None
    assert _top_period_contract_violation(two_step) is None

# --- 选期解析（系统先算出来，再变成确定性 WHERE） ----------------------------


def test_build_top_period_sql_shape() -> None:
    from app.knowledge import registry as knowledge
    from app.services.metric_preview_service import build_top_period_sql

    metric = next(m for m in knowledge.all_metrics() if m.name == "销售额")
    built = build_top_period_sql(
        metric, unit="month", direction="max", time_range="2024年", scope=["华东"],
    )

    assert built is not None
    sql, bucket = built
    assert bucket == "dim_date.month"
    assert "GROUP BY dim_date.year, dim_date.month" in sql
    assert "ORDER BY value DESC" in sql
    assert sql.rstrip().endswith("LIMIT 1")
    assert "dim_store.region IN ('华东')" in sql


def test_build_top_period_sql_fail_open_on_unknown_range() -> None:
    from app.knowledge import registry as knowledge
    from app.services.metric_preview_service import build_top_period_sql

    metric = next(m for m in knowledge.all_metrics() if m.name == "销售额")
    assert build_top_period_sql(
        metric, unit="month", direction="max", time_range=None,
    ) is None


def test_resolved_top_period_requires_period_filter_in_sql() -> None:
    from app.agent.sql_graph import _top_period_contract_violation

    spec = {
        "direction": "max", "unit": "month", "resolved": True,
        "period_column": "dim_date.month", "period_value": "11",
        "label": "2024年11月",
    }
    flat = (
        "SELECT dim_date.year AS 年份, SUM(fact_orders.order_amount) AS 销售额 "
        "FROM fact_orders GROUP BY dim_date.year"
    )
    filtered = (
        "SELECT dim_date.full_date AS 日期, fact_orders.order_amount AS 订单金额 "
        "FROM fact_orders WHERE dim_date.year = 2024 AND dim_date.month = 11 LIMIT 200"
    )

    assert _top_period_contract_violation(flat, spec) is not None
    assert _top_period_contract_violation(filtered, spec) is None


def test_resolved_hint_writes_literal_filters(monkeypatch) -> None:
    """算好期之后，指令里必须是可直接照抄的 WHERE，而不是让模型自己写 CTE。"""
    from app.agent.confirmed_execution_graph import _format_confirmed_requirement
    from app.models.requirement import RequirementCard, RequirementTopPeriod
    from app.services.metric_preview_service import TopPeriodResult

    card = RequirementCard(
        id="d1",
        status="complete",
        summary="2024年华东销售额最高的月份明细",
        target_metrics=["销售额"],
        time_range="2024年",
        granularity="month",
        scope=["华东"],
        detail_level="detail",
        top_period=RequirementTopPeriod(direction="max", unit="month", metric="销售额"),
    )
    resolved = TopPeriodResult(
        ok=True, unit="month", label="2024年11月",
        filters=("dim_date.year = 2024", "dim_date.month = 11"),
        period_column="dim_date.month", period_value="11",
    )

    text = _format_confirmed_requirement(card, top_resolved=resolved) or ""

    assert "2024年11月" in text
    assert "dim_date.month = 11" in text
    assert "LIMIT 1" not in text          # 不再要求模型自己选期
    assert "禁止" in text and "GROUP BY" in text
