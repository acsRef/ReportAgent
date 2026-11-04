"""口径预览服务与图节点（plan 2026-11-04-requirement-slot-contract Phase 2）。

两件要钉住的事：
1. **SQL 只由注册表目录拼装**（CLAUDE.md §4.1）：认不出的形状一律返回 None，
   由调用方 fail-open 退回纯文字——绝不拿错区间/错表算出一个「看着对」的数；
2. **失败不阻塞澄清**：超时/报错/空结果 → 选项保持纯文字。
"""
from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest

from app.agent import requirement_analysis_graph as gag
from app.knowledge import registry as knowledge
from app.models.requirement import (
    RequirementCard,
    RequirementMissingField,
    RequirementOption,
)
from app.services import metric_preview_service as preview

pytestmark = pytest.mark.graphs

TODAY = datetime.date(2026, 11, 4)


def _metric(name: str):
    metric = knowledge.resolve_metric(name)
    assert metric is not None, name
    return metric


# --- 时间范围解析 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("time_range", "expected"),
    [
        ("2024年", (datetime.date(2024, 1, 1), datetime.date(2025, 1, 1))),
        ("2024年7月", (datetime.date(2024, 7, 1), datetime.date(2024, 8, 1))),
        ("2024年7月-9月", (datetime.date(2024, 7, 1), datetime.date(2024, 10, 1))),
        ("2026-08-03至2026-11-03", (datetime.date(2026, 8, 3), datetime.date(2026, 11, 4))),
        ("今年", (datetime.date(2026, 1, 1), datetime.date(2027, 1, 1))),
        ("去年", (datetime.date(2025, 1, 1), datetime.date(2026, 1, 1))),
        ("上个季度", None),
        (None, None),
    ],
)
def test_resolve_period_bounds(time_range, expected) -> None:
    assert preview.resolve_period_bounds(time_range, TODAY) == expected


# --- SQL 拼装（注册表驱动） -------------------------------------------------


def test_preview_sql_without_scope() -> None:
    sql = preview.build_preview_sql(_metric("销售额"), time_range="2024年", today=TODAY)

    assert sql is not None
    assert "SUM(fact_orders.order_amount) AS value" in sql
    assert "FROM fact_orders" in sql
    assert "fact_orders.order_date >= '2024-01-01'" in sql
    assert "fact_orders.order_date < '2025-01-01'" in sql
    assert "GROUP BY" not in sql.upper()


def test_preview_sql_with_region_scope_joins_dim_store() -> None:
    sql = preview.build_preview_sql(
        _metric("销售额"), time_range="2024年", scope=["华东"], today=TODAY,
    )

    assert sql is not None
    assert "LEFT JOIN dim_store ON dim_store.store_id = fact_orders.store_id" in sql
    assert "dim_store.region IN ('华东')" in sql


def test_preview_sql_declares_metric_joins() -> None:
    """净销售额依赖退款子查询——预览必须把注册表里的 joins 一起带上。"""
    sql = preview.build_preview_sql(_metric("净销售额"), time_range="2024年", today=TODAY)

    assert sql is not None
    assert "refund.refund_amount" in sql
    assert "LEFT JOIN (" in sql


def test_preview_sql_fail_open_when_scope_has_no_fk_path() -> None:
    """实收金额走 fact_payments，注册表没声明到 dim_store 的链路 → 不能安静忽略范围。"""
    assert preview.build_preview_sql(
        _metric("实收金额"), time_range="2024年", scope=["华东"], today=TODAY,
    ) is None


def test_preview_sql_fail_open_on_unknown_period() -> None:
    assert preview.build_preview_sql(
        _metric("销售额"), time_range="上个季度", today=TODAY,
    ) is None


# --- 执行路径（fail-open） --------------------------------------------------


def _fake_execute(payload_by_expr: dict[str, object], *, raises: bool = False):
    def _execute(sql: str) -> str:
        if raises:
            raise RuntimeError("db down")
        for needle, value in payload_by_expr.items():
            if needle in sql:
                return json.dumps({"columns": ["value"], "rows": [{"value": value}]})
        return json.dumps({"columns": ["value"], "rows": [{"value": None}]})

    return _execute


async def test_preview_metrics_returns_values(monkeypatch) -> None:
    from app.tools import sql_tools

    monkeypatch.setattr(
        sql_tools, "execute_sql",
        _fake_execute({
            "fact_orders.order_amount) AS value": "260606011.42",
            "refund.refund_amount": "241000000.00",
        }),
    )
    result = await preview.preview_metrics(
        [_metric("销售额"), _metric("净销售额")],
        time_range="2024年", scope=[], today=TODAY,
    )

    assert result.ok
    values = {item.name: item.value for item in result.items}
    assert values["销售额"] == Decimal("260606011.42")
    assert values["净销售额"] == Decimal("241000000.00")


async def test_preview_metrics_fail_open_on_error(monkeypatch) -> None:
    from app.tools import sql_tools

    monkeypatch.setattr(sql_tools, "execute_sql", _fake_execute({}, raises=True))
    result = await preview.preview_metrics(
        [_metric("销售额"), _metric("净销售额")], time_range="2024年", today=TODAY,
    )

    assert result.ok is False
    assert all(not item.ok for item in result.items)


async def test_preview_metrics_fail_open_on_unsupported_shape() -> None:
    result = await preview.preview_metrics(
        [_metric("实收金额")], time_range="2024年", scope=["华东"], today=TODAY,
    )

    assert result.ok is False
    assert result.reason == "unsupported_shape"


def test_format_hint_includes_amount_and_note() -> None:
    item = preview.MetricPreview(name="销售额", ok=True, value=Decimal("260606011.42"))

    hint = preview.format_hint(item, _metric("销售额"), base_note="未扣退款")

    assert "2.61 亿" in hint
    assert "未扣退款" in hint


def test_format_hint_keeps_note_when_not_ok() -> None:
    item = preview.MetricPreview(name="销售额", ok=False)
    assert preview.format_hint(item, _metric("销售额"), base_note="未扣退款") == "未扣退款"


# --- 图节点 -----------------------------------------------------------------


def _card_with_metric_ambiguity() -> RequirementCard:
    return RequirementCard(
        id="draft-preview",
        version=1,
        status="missing",
        summary="2024年各区域表现",
        time_range="2024年",
        target_metrics=[],
        slot_states={"metric": "ambiguous"},
        slot_order=["metric"],
        missing_fields=[
            RequirementMissingField(
                key="metric",
                label="用哪个口径？",
                state="ambiguous",
                options=[
                    RequirementOption(label="实收金额", value="metric:实收金额", hint="仅统计支付成功的流水"),
                    RequirementOption(label="销售额", value="metric:销售额", hint="订单金额口径，未扣退款"),
                ],
            )
        ],
    )


async def test_preview_node_fills_numeric_hints(monkeypatch) -> None:
    async def fake_preview(metrics, *, time_range, scope=None, **kwargs):
        assert time_range == "2024年"
        return preview.PreviewResult(ok=True, items=tuple(
            preview.MetricPreview(name=m.name, ok=True, value=Decimal("100000000"))
            for m in metrics
        ))

    monkeypatch.setattr(
        "app.services.metric_preview_service.preview_metrics", fake_preview,
    )

    out = await gag._requirement_preview({"requirement_card": _card_with_metric_ambiguity()})

    card = out["requirement_card"]
    field = next(mf for mf in card.missing_fields if mf.key == "metric")
    assert all("1.00 亿" in (opt.hint or "") for opt in field.options)
    # value/label 保持稳定（前端据此回填 selected_value）
    assert [opt.value for opt in field.options] == ["metric:实收金额", "metric:销售额"]


async def test_preview_node_keeps_text_when_preview_fails(monkeypatch) -> None:
    async def fake_preview(metrics, **kwargs):
        return preview.PreviewResult(ok=False, reason="timeout")

    monkeypatch.setattr(
        "app.services.metric_preview_service.preview_metrics", fake_preview,
    )

    out = await gag._requirement_preview({"requirement_card": _card_with_metric_ambiguity()})

    assert out == {}, "预览失败必须静默跳过，不阻塞澄清"


async def test_preview_node_skips_when_no_ambiguity(monkeypatch) -> None:
    called = False

    async def fake_preview(metrics, **kwargs):
        nonlocal called
        called = True
        return preview.PreviewResult(ok=True)

    monkeypatch.setattr(
        "app.services.metric_preview_service.preview_metrics", fake_preview,
    )
    card = _card_with_metric_ambiguity().model_copy(update={"missing_fields": []})

    assert await gag._requirement_preview({"requirement_card": card}) == {}
    assert called is False
