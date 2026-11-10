"""子任务诊断与复杂度分档的契约（plan 2026-11-10-complexity-and-subtask-diagnostics）。

重点是**用本轮真机上真实出现过的坏 SQL 反证诊断器有效**——只证明「全绿时不报错」
没有意义，要证明「当初那种错会被指到正确的子任务上」。
"""
from __future__ import annotations

import pytest

from evaluation.task_decomposition import (
    SUBTASK_ORDER,
    complexity_tier,
    decompose,
    diagnose,
    subtask_failure_counts,
    tier_accuracy,
)

# 金标：2024 各区域销售额（毛额）
GOLD_REGION = (
    "SELECT s.region AS region, ROUND(SUM(o.order_amount), 2) AS sales "
    "FROM fact_orders o JOIN dim_store s ON s.store_id = o.store_id "
    "JOIN dim_date d ON d.full_date = o.order_date "
    "WHERE d.year = 2024 GROUP BY s.region ORDER BY sales DESC"
)

# 真机坏 SQL ①：没有年份过滤 + 换成净销售额（四年一起算 + 毛额变净额）
BAD_FOUR_YEARS_NET = (
    "SELECT dim_store.region AS 区域, "
    "SUM(fact_orders.order_amount) - COALESCE(SUM(fact_payments.payment_amount), 0) AS 净销售额 "
    "FROM fact_orders "
    "LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id "
    "LEFT JOIN fact_payments ON fact_orders.order_id = fact_payments.order_id "
    "AND fact_payments.status = 'REFUNDED' "
    "GROUP BY dim_store.region"
)

# 金标：2024 华东每月销售额
GOLD_MONTH_EAST = (
    "SELECT d.month AS month, ROUND(SUM(o.order_amount), 2) AS sales "
    "FROM fact_orders o JOIN dim_store s ON s.store_id = o.store_id "
    "JOIN dim_date d ON d.full_date = o.order_date "
    "WHERE d.year = 2024 AND s.region = '华东' GROUP BY d.month ORDER BY d.month"
)

# 真机坏 SQL ②：区域过滤丢失（算成全国各月）
BAD_NO_REGION = (
    "SELECT d.month AS month, ROUND(SUM(o.order_amount), 2) AS sales "
    "FROM fact_orders o JOIN dim_date d ON d.full_date = o.order_date "
    "WHERE d.year = 2024 GROUP BY d.month ORDER BY d.month"
)


class TestDecompose:
    def test_parts_of_a_star_join_query(self):
        parts = decompose(GOLD_REGION)
        assert parts is not None
        assert parts.tables == ("dim_date", "dim_store", "fact_orders")
        # 别名已解析成真实表名，金标写 o/s/d 也不影响
        assert set(parts.join_keys) == {
            "dim_store.store_id=fact_orders.store_id",
            "dim_date.full_date=fact_orders.order_date",
        }
        assert "sum(fact_orders.order_amount)" in parts.columns
        # 维度列只比列名（表前缀/别名属写法差异；CTE 投影也没法解析到物理表）
        assert "region" in parts.columns
        # 谓词去表限定（别名不同不算差异）
        assert parts.predicates == ("year = 2024",)
        assert parts.uses_cte is False

    def test_unparseable_sql_is_unknown_not_wrong(self):
        assert decompose("SELECT FROM WHERE") is None
        assert decompose("") is None
        assert decompose("UPDATE fact_orders SET order_amount = 0") is None

    def test_detail_query_has_no_aggregate(self):
        parts = decompose(
            "SELECT o.order_id, o.order_date, s.region FROM fact_orders o "
            "JOIN dim_store s ON s.store_id = o.store_id LIMIT 200"
        )
        assert parts is not None
        assert parts.has_aggregate is False
        assert complexity_tier(
            "SELECT o.order_id, o.order_date FROM fact_orders o LIMIT 200"
        ) == 3


class TestDiagnoseRealIncidents:
    """三个真机事故各自必须被指到对的子任务上。"""

    def test_missing_year_and_wrong_metric(self):
        diag = diagnose(GOLD_REGION, BAD_FOUR_YEARS_NET)
        assert diag is not None
        assert diag.ok is False
        # 年份谓词丢了 → predicates 必须报
        assert "predicates" in diag.failed
        # 金标要的 dim_date join 没了 → join_keys 必须报
        assert "join_keys" in diag.failed
        assert "year = 2024" in diag.details["predicates"]["missing"]
        # 丢一个过滤条件，连带它需要的维表也没 join 了 → tables 也会报（上游优先属正常）
        assert "dim_date" in diag.details["tables"]["missing"]
        # 多出来的 fact_payments 只记录，不算「表没找对」（missing 为空才判 tables 失败）
        assert "fact_payments" in diag.details["tables"]["extra"]

    def test_missing_region_predicate(self):
        diag = diagnose(GOLD_MONTH_EAST, BAD_NO_REGION)
        assert diag is not None
        assert "predicates" in diag.failed
        assert "region = '华东'" in diag.details["predicates"]["missing"]
        # 同上：区域维表也随之消失，所以 tables 同时报——这是**真实差异**，不是误报
        assert "dim_store" in diag.details["tables"]["missing"]

    def test_half_of_a_two_metric_question(self):
        gold = (
            "WITH sales AS (SELECT p.category AS category, SUM(o.order_amount) AS amount "
            "FROM fact_orders o JOIN dim_product p ON p.product_id = o.product_id "
            "JOIN dim_date d ON d.full_date = o.order_date WHERE d.year = 2024 "
            "GROUP BY p.category), refunds AS (SELECT p.category AS category, "
            "SUM(pay.payment_amount) AS amount FROM fact_payments pay "
            "JOIN fact_orders o ON o.order_id = pay.order_id "
            "JOIN dim_product p ON p.product_id = o.product_id "
            "JOIN dim_date d ON d.full_date = o.order_date "
            "WHERE d.year = 2024 AND pay.status = 'REFUNDED' GROUP BY p.category) "
            "SELECT s.category AS category, ROUND(s.amount, 2) AS sales, "
            "ROUND(COALESCE(r.amount, 0), 2) AS refund "
            "FROM sales s LEFT JOIN refunds r ON r.category = s.category ORDER BY sales DESC"
        )
        bad = (
            "SELECT dim_product.category AS 品类, "
            "SUM(fact_payments.payment_amount) FILTER (WHERE fact_payments.status = 'REFUNDED') "
            "AS 退款金额 FROM fact_payments "
            "LEFT JOIN fact_orders ON fact_payments.order_id = fact_orders.order_id "
            "LEFT JOIN dim_product ON fact_orders.product_id = dim_product.product_id "
            "LEFT JOIN dim_date ON fact_payments.payment_date = dim_date.full_date "
            "WHERE dim_date.year = 2024 GROUP BY dim_product.category"
        )
        diag = diagnose(gold, bad)
        assert diag is not None
        # 少了一个口径 → 列映射必须报
        assert "columns" in diag.failed
        assert any(
            "fact_orders.order_amount" in item
            for item in diag.details["columns"]["missing"]
        )
        # 「有没有用 CTE」不算失败：扁平写法与预聚合写法可以给出同样的结果
        assert "structure" not in diag.failed

    def test_identical_sql_is_clean(self):
        diag = diagnose(GOLD_REGION, GOLD_REGION)
        assert diag is not None
        assert diag.ok is True
        assert diag.failed == ()
        assert diag.details == {}


class TestComplexityTier:
    def test_tier1_single_table(self):
        assert complexity_tier("SELECT SUM(quantity) AS qty FROM fact_orders") == 1

    def test_tier2_star_join(self):
        assert complexity_tier(GOLD_REGION) == 2

    def test_tier3_preaggregation(self):
        assert complexity_tier(
            "WITH s AS (SELECT store_id, SUM(order_amount) AS amt FROM fact_orders "
            "GROUP BY store_id) SELECT store_id, amt FROM s"
        ) == 3

    def test_tier3_multi_source(self):
        assert complexity_tier(
            "SELECT SUM(fact_orders.order_amount) - "
            "COALESCE(SUM(refund.amount), 0) AS net FROM fact_orders "
            "LEFT JOIN (SELECT order_id, SUM(payment_amount) AS amount FROM fact_payments "
            "GROUP BY order_id) refund ON refund.order_id = fact_orders.order_id"
        ) == 3

    def test_unknown_tier_for_unparseable(self):
        assert complexity_tier("not sql at all") is None


class TestAggregates:
    ROWS = [
        {"complexity_tier": 2, "result_matches": True, "diagnosis": {"failed": []}},
        {"complexity_tier": 2, "result_matches": False,
         "diagnosis": {"failed": ["predicates"]}},
        {"complexity_tier": 3, "result_matches": False,
         "diagnosis": {"failed": ["columns", "structure"]}},
        {"complexity_tier": None, "result_matches": None, "diagnosis": None},
        {"complexity_tier": 2, "result_matches": None},   # 有分档但无金标 → 不计入
        # 结果一致但结构与金标不同（扁平 vs 预聚合）→ 是合法替代写法，不许算「失败归因」
        {"complexity_tier": 3, "result_matches": True,
         "diagnosis": {"failed": ["join_keys"]}},
    ]

    def test_tier_accuracy_ignores_ungraded(self):
        tiers = tier_accuracy(self.ROWS)
        assert tiers["T2"] == {"cases": 2, "matched": 1, "accuracy": 0.5}
        assert tiers["T3"] == {"cases": 2, "matched": 1, "accuracy": 0.5}
        assert "T1" not in tiers

    def test_subtask_failures_counts_first_failure_only(self):
        counts = subtask_failure_counts(self.ROWS)
        # 「columns + structure」只记更上游的 columns；结果一致的那行不参与归因
        assert counts == {"predicates": 1, "columns": 1}

    def test_subtask_order_is_upstream_first(self):
        assert SUBTASK_ORDER[0] == "tables"
        assert SUBTASK_ORDER[-1] == "structure"
