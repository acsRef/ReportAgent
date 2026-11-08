"""明细模式的确定性契约（2026-11-03）。

实测（多轮 e2e + 手测）：卡上 `detail_level=detail`、plan 阶段维度已清空、生成 prompt
也明写「禁止 SUM/COUNT 等聚合」，模型仍然写出 `SUM(order_amount)` 且无 GROUP BY
→ 返回 1 行汇总，而用户要的是逐条数据。光靠 prompt 治不住采样，所以在
`sql_validate` 之后加一道确定性拦截：违规按 validation 失败处理，交给既有 repair 环。
"""
from __future__ import annotations

import json

import pytest

from app.agent import sql_graph
from app.agent.sql_graph import _detail_contract_violation, _validate

pytestmark = pytest.mark.graphs


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(order_amount) FROM fact_orders",
        "SELECT count(*) FROM fact_orders",
        "SELECT avg(payment_amount) FROM fact_payments",
        "select max(order_amount), min(order_amount) from fact_orders",
    ],
)
def test_aggregate_detected(sql: str) -> None:
    assert _detail_contract_violation(sql) is not None


def test_group_by_detected() -> None:
    sql = "SELECT region, payment_amount FROM fact_payments JOIN dim_store USING (store_id) GROUP BY region, payment_amount"
    assert "GROUP BY" in (_detail_contract_violation(sql) or "")


def test_plain_detail_sql_passes() -> None:
    sql = (
        "SELECT dim_date.full_date AS 日期, dim_store.region AS 区域, "
        "fact_orders.order_amount AS 销售额 FROM fact_orders "
        "LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date "
        "ORDER BY dim_date.full_date DESC LIMIT 200"
    )
    assert _detail_contract_violation(sql) is None


def test_validate_routes_detail_violation_to_repair(monkeypatch) -> None:
    """SQL 合法但形状违反明细契约 → validation 失败（走既有 repair），不是成功。"""

    def fake_validate_sql(sql: str) -> str:
        return json.dumps({"valid": True, "error": "", "error_kind": None})

    monkeypatch.setattr(sql_graph, "validate_sql", fake_validate_sql)
    state = {
        "generated_sql": "SELECT SUM(order_amount) AS 销售额 FROM fact_orders",
        "detail_level": "detail",
    }

    out = _validate(state)

    assert out["validation_result"]["valid"] is False
    assert out["validation_result"]["error_kind"] == "syntax"
    assert "明细模式" in out["validation_result"]["error"]


def test_validate_keeps_aggregate_legal_when_not_detail(monkeypatch) -> None:
    """非明细模式下的聚合是正常的（按周/月汇总），不许被误拦。"""

    def fake_validate_sql(sql: str) -> str:
        return json.dumps({"valid": True, "error": "", "error_kind": None})

    monkeypatch.setattr(sql_graph, "validate_sql", fake_validate_sql)
    state = {
        "generated_sql": "SELECT week_of_year, SUM(order_amount) FROM fact_orders GROUP BY week_of_year",
        "detail_level": None,
    }

    out = _validate(state)

    assert out["validation_result"]["valid"] is True


def test_validate_detail_ok_passes(monkeypatch) -> None:
    def fake_validate_sql(sql: str) -> str:
        return json.dumps({"valid": True, "error": "", "error_kind": None})

    monkeypatch.setattr(sql_graph, "validate_sql", fake_validate_sql)
    state = {
        # 2026-11-08（plan clarification-option-prefixes D2）：明细列数下限抬到 3 列，
        # 原来的「full_date + order_amount」两列现在按违规处理（正是真机里「一列数」的形态）。
        "generated_sql": (
            "SELECT dim_date.full_date AS 日期, dim_store.region AS 区域, "
            "fact_orders.order_amount AS 金额 FROM fact_orders "
            "LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date "
            "LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id "
            "ORDER BY 1 DESC LIMIT 200"
        ),
        "detail_level": "detail",
    }

    out = _validate(state)

    assert out["validation_result"]["valid"] is True

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT fact_orders.quantity AS 销量 FROM fact_orders LIMIT 200",
        "SELECT order_amount FROM fact_orders ORDER BY order_date DESC LIMIT 200",
    ],
)
def test_detail_without_dimension_column_is_violation(sql: str) -> None:
    """明细只有指标列 → 违规（2026-11-04 实测：表格只剩一列、图表画成噪声柱）。"""
    assert "维度列" in (_detail_contract_violation(sql) or "")


def test_detail_with_dimension_column_passes() -> None:
    sql = (
        "SELECT dim_date.full_date AS 日期, dim_store.region AS 区域, "
        "fact_orders.quantity AS 销量 FROM fact_orders "
        "LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date "
        "ORDER BY 日期 DESC LIMIT 200"
    )
    assert _detail_contract_violation(sql) is None
