"""取值过滤不许丢失、也不许写进外连接 ON（2026-11-11，plan sql-filter-in-join-on）。

真机会话 `c3f0cf93…` 两轮报告都错，用户唯一看得见的线索是核心发现里出现「**None月** 最高」：

1. 第一轮「分析下去年华南区域的销售」→ SQL **一个年份谓词都没有** → 华南 2023–2026
   四年合计 1.84 亿（2025 年真值 4,784 万）；
2. 第二轮「每个月的情况也都说一下吧」→ SQL 把 `region='华南'`、`year=2026` 写进
   `LEFT JOIN ... ON`：外连接匹配不上仍保留左表整行，于是月度行是**全国** 2026、
   `month=NULL` 一行是**全国**非 2026，结论合计 9.38 亿（华南 1–10 月真值 3,936 万）。

两道闸都以 plan 要求的取值为锚——只拦「用户明确要求的那个过滤」，不做「外连接不许有
常量」的全局禁令（那会误杀 `AND p.active = true` 这类合法外连接条件）。
"""
from __future__ import annotations

import json

import pytest

from app.agent import sql_graph
from app.agent.sql_graph import (
    _join_filter_contract_violation,
    _missing_filter_violation,
    _required_filter_values,
    _validate,
)

pytestmark = pytest.mark.graphs


class _Plan:
    """QueryPlan 的最小替身（只读 time_range / filters）。"""

    def __init__(self, time_range: str | None = None, filters: list | None = None) -> None:
        self.time_range = time_range
        self.filters = filters or []


# 真机事故里那条 SQL（region / year 都在 ON 里）
REAL_INCIDENT_SQL = """
SELECT dim_date.month AS 月份, SUM(fact_orders.order_amount) AS 销售额
FROM fact_orders
LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id
     AND dim_store.region = '华南'
LEFT JOIN dim_date ON dim_date.full_date = fact_orders.order_date
     AND dim_date.year = 2026
GROUP BY dim_date.month
"""

# 第一轮那条：有区域过滤、没有任何年份过滤
V1_SQL = """
SELECT SUM(fact_orders.order_amount) AS 销售额
FROM fact_orders
LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id
WHERE dim_store.region = '华南'
"""

CORRECT_SQL = """
SELECT d.month AS 月份, SUM(o.order_amount) AS 销售额
FROM fact_orders o
JOIN dim_store s ON s.store_id = o.store_id
JOIN dim_date d ON d.full_date = o.order_date
WHERE s.region = '华南' AND d.year = 2025
GROUP BY d.month
"""


# --- 锚点：只收「用户明确要求的过滤取值」 -----------------------------------


def test_required_values_collect_year_and_scope() -> None:
    plan = _Plan("2026年", [{"field": "region", "operator": "=", "value": "华南"}])

    assert _required_filter_values(plan) == ["2026", "华南"]


def test_required_values_accept_string_filter_form() -> None:
    """模型偶尔把 filters 写成字符串（`区域=华南`），取值仍要能取到。"""
    plan = _Plan(None, ["区域=华南", "region='华东'"])

    assert _required_filter_values(plan) == ["华南", "华东"]


def test_required_values_skip_thresholds_and_relative_time() -> None:
    """阈值（10000）不是范围过滤；`今年` 没有 4 位年份，不产生锚点。"""
    plan = _Plan("今年", [{"field": "order_amount", "operator": ">", "value": "10000"}])

    assert _required_filter_values(plan) == []


def test_required_values_handle_missing_plan() -> None:
    assert _required_filter_values(None) == []


def test_placeholder_values_are_not_requirements() -> None:
    """「全部/不限」不是真过滤——要求它出现在 SQL 里只会逼 repair 空转。"""
    plan = _Plan(None, [{"field": "region", "operator": "=", "value": "全部"}])

    assert _required_filter_values(plan) == []


def test_long_value_matches_by_prefix() -> None:
    """plan 写「华南区域」、SQL 写 `'华南'` 是同一件事，不算缺失。"""
    plan = _Plan(None, [{"field": "region", "operator": "=", "value": "华南区域"}])
    sql = "SELECT SUM(order_amount) FROM fact_orders o JOIN dim_store s ON s.store_id = o.store_id WHERE s.region = '华南'"

    assert _missing_filter_violation(sql, plan) is None


# --- C1：要求的过滤必须真的出现在 SQL 里 ------------------------------------


def test_missing_year_flagged() -> None:
    """第一轮事故：区域过滤有、年份过滤整段缺失 → 四年一起算。"""
    plan = _Plan("2025年", [{"field": "region", "operator": "=", "value": "华南"}])

    violation = _missing_filter_violation(V1_SQL, plan)

    assert violation is not None
    assert "2025" in violation


def test_missing_scope_flagged() -> None:
    plan = _Plan("2025年", [{"field": "region", "operator": "=", "value": "华南"}])
    sql = "SELECT d.month, SUM(o.order_amount) FROM fact_orders o JOIN dim_date d ON d.full_date = o.order_date WHERE d.year = 2025 GROUP BY d.month"

    violation = _missing_filter_violation(sql, plan)

    assert violation is not None
    assert "华南" in violation


def test_year_boundaries_not_confused() -> None:
    """`120250` 之类不能被当成 2025 出现过。"""
    plan = _Plan("2025年")
    sql = "SELECT SUM(order_amount) FROM fact_orders WHERE order_id = 120250"

    assert _missing_filter_violation(sql, plan) is not None


def test_missing_filter_passes_when_present() -> None:
    plan = _Plan("2025年", [{"field": "region", "operator": "=", "value": "华南"}])

    assert _missing_filter_violation(CORRECT_SQL, plan) is None


def test_between_dates_satisfies_year_requirement() -> None:
    """年份也可以用日期区间表达——不算缺失。"""
    plan = _Plan("2025年")
    sql = "SELECT SUM(order_amount) FROM fact_orders WHERE order_date BETWEEN '2025-01-01' AND '2025-12-31'"

    assert _missing_filter_violation(sql, plan) is None


# --- C2：要求的过滤不许写进外连接 ON ----------------------------------------


def test_scope_in_left_join_on_flagged() -> None:
    """真机事故：`AND dim_store.region = '华南'` 写在 LEFT JOIN 的 ON 里。"""
    plan = _Plan("2026年", [{"field": "region", "operator": "=", "value": "华南"}])

    violation = _join_filter_contract_violation(REAL_INCIDENT_SQL, plan)

    assert violation is not None
    assert "华南" in violation
    assert "WHERE" in violation  # 必须说清怎么改


def test_year_in_left_join_on_flagged() -> None:
    plan = _Plan("2026年")
    sql = (
        "SELECT d.month, SUM(o.order_amount) FROM fact_orders o "
        "LEFT JOIN dim_date d ON d.full_date = o.order_date AND d.year = 2026 "
        "GROUP BY d.month"
    )

    violation = _join_filter_contract_violation(sql, plan)

    assert violation is not None
    assert "2026" in violation


def test_legit_outer_join_predicate_not_flagged() -> None:
    """误报防线：`AND p.active = true` 是合法外连接条件（匹配活跃促销，否则留空）。"""
    plan = _Plan("2025年")
    sql = (
        "SELECT p.promotion_name, SUM(o.order_amount) FROM fact_orders o "
        "LEFT JOIN dim_promotion p ON p.promotion_id = o.promotion_id AND p.active = true "
        "WHERE o.order_date >= '2025-01-01' GROUP BY p.promotion_name"
    )

    assert _join_filter_contract_violation(sql, plan) is None


def test_inner_join_const_not_flagged() -> None:
    """INNER JOIN 的 ON 与 WHERE 等价，不判。"""
    plan = _Plan("2026年", [{"field": "region", "operator": "=", "value": "华南"}])
    sql = (
        "SELECT s.region, SUM(o.order_amount) FROM fact_orders o "
        "JOIN dim_store s ON s.store_id = o.store_id AND s.region = '华南' "
        "JOIN dim_date d ON d.full_date = o.order_date AND d.year = 2026 "
        "GROUP BY s.region"
    )

    assert _join_filter_contract_violation(sql, plan) is None


def test_value_not_required_not_flagged() -> None:
    """SQL 的 ON 里有常量，但 plan 没要求这个过滤 → 不是我们的问题，不拦。"""
    plan = _Plan("2026年")
    sql = (
        "SELECT s.region, SUM(o.order_amount) FROM fact_orders o "
        "LEFT JOIN dim_store s ON s.store_id = o.store_id AND s.region = '华南' "
        "GROUP BY s.region"
    )

    assert _join_filter_contract_violation(sql, plan) is None


def test_correct_sql_passes_both() -> None:
    plan = _Plan("2025年", [{"field": "region", "operator": "=", "value": "华南"}])

    assert _missing_filter_violation(CORRECT_SQL, plan) is None
    assert _join_filter_contract_violation(CORRECT_SQL, plan) is None


# --- 接线：违规必须走既有 repair 环，而不是当成功返回 ------------------------


def _stub_validate_sql(monkeypatch) -> None:
    def fake_validate_sql(sql: str) -> str:
        return json.dumps({"valid": True, "error": "", "error_kind": None})

    monkeypatch.setattr(sql_graph, "validate_sql", fake_validate_sql)


def test_validate_routes_missing_filter_to_repair(monkeypatch) -> None:
    _stub_validate_sql(monkeypatch)
    state = {
        "generated_sql": V1_SQL,
        "query_plan": _Plan("2025年", [{"field": "region", "operator": "=", "value": "华南"}]),
    }

    out = _validate(state)

    assert out["validation_result"]["valid"] is False
    assert out["validation_result"]["error_kind"] == "syntax"  # 复用既有 repair 路由
    assert "2025" in out["validation_result"]["error"]


def test_validate_routes_join_on_filter_to_repair(monkeypatch) -> None:
    _stub_validate_sql(monkeypatch)
    state = {
        "generated_sql": REAL_INCIDENT_SQL,
        "query_plan": _Plan("2026年", [{"field": "region", "operator": "=", "value": "华南"}]),
    }

    out = _validate(state)

    assert out["validation_result"]["valid"] is False
    assert "ON" in out["validation_result"]["error"]


def test_validate_keeps_correct_sql_valid(monkeypatch) -> None:
    _stub_validate_sql(monkeypatch)
    state = {
        "generated_sql": CORRECT_SQL,
        "query_plan": _Plan("2025年", [{"field": "region", "operator": "=", "value": "华南"}]),
    }

    out = _validate(state)

    assert out["validation_result"]["valid"] is True


def test_validate_without_plan_is_untouched(monkeypatch) -> None:
    """没有 plan 的场景（工具直调 / 老链路）不引入新约束。"""
    _stub_validate_sql(monkeypatch)
    state = {"generated_sql": V1_SQL}

    out = _validate(state)

    assert out["validation_result"]["valid"] is True
