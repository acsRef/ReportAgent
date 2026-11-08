"""明细投影的列数契约（plan 2026-11-08-clarification-option-prefixes D2）。

真机：「2023年11月的订单明细」给出 4 列（日期/区域/品类/金额），缺数量、缺订单号；
而契约此前只要求「至少一个日期/维度列」——`日期 + 金额` 两列也能过闸，
用户看到的其实是「一列数」。这里把列数下限钉成 3（日期 + 维度列 + 指标列）。
"""
from __future__ import annotations

import pytest

from app.agent.sql_graph import (
    _MIN_DETAIL_COLUMNS,
    _count_select_columns,
    _detail_contract_violation,
)

pytestmark = pytest.mark.contracts


def _detail(*columns: str) -> str:
    return (
        f"SELECT {', '.join(columns)} FROM fact_orders "
        "LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date "
        "LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id "
        "ORDER BY 1 DESC LIMIT 200"
    )


class TestColumnCount:
    def test_数顶层逗号(self) -> None:
        assert _count_select_columns("SELECT a, b, c FROM t") == 3
        assert _count_select_columns("SELECT a FROM t") == 1

    def test_函数里的逗号不算列(self) -> None:
        assert _count_select_columns("SELECT COALESCE(a, b), c FROM t") == 2

    def test_没有_from_也数得出来(self) -> None:
        assert _count_select_columns("SELECT 1, 2") == 2

    def test_空投影是零(self) -> None:
        assert _count_select_columns("") == 0
        assert _count_select_columns("SELECT FROM t") == 0


class TestContract:
    def test_两列判违规(self) -> None:
        violation = _detail_contract_violation(
            _detail("dim_date.full_date AS 日期", "fact_orders.order_amount AS 金额"),
        )

        assert violation is not None
        assert "至少" in violation
        assert "维度列" in violation          # 既有用例按这个词断言，不能丢

    def test_三列通过(self) -> None:
        sql = _detail(
            "dim_date.full_date AS 日期",
            "dim_store.region AS 区域",
            "fact_orders.order_amount AS 金额",
        )
        assert _detail_contract_violation(sql) is None

    def test_列数下限是_3(self) -> None:
        assert _MIN_DETAIL_COLUMNS == 3

    def test_聚合优先报聚合(self) -> None:
        """一列聚合同时满足「列少」和「聚合」，文案要说最根本的那个。"""
        violation = _detail_contract_violation(
            "SELECT SUM(fact_orders.order_amount) AS 销售额 FROM fact_orders",
        )

        assert violation is not None
        assert "禁止聚合" in violation

    def test_CTE_里的聚合不算(self) -> None:
        sql = (
            "WITH picked AS (SELECT dim_date.month AS m, SUM(fact_orders.order_amount) AS v "
            "FROM fact_orders GROUP BY 1 ORDER BY v DESC LIMIT 1) "
            "SELECT dim_date.full_date, dim_store.region, fact_orders.order_amount "
            "FROM fact_orders "
            "LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date "
            "LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id "
            "LIMIT 200"
        )
        assert _detail_contract_violation(sql, allow_cte_aggregate=True) is None
