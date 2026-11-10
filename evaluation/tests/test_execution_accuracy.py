"""Execution Accuracy 比较器契约（纯函数，无 DB / 无网络）。

对应 BEAVER（arXiv 2409.02038）的核心立场：**唯一闭环判据是执行结果与金标一致**，
检索/召回只是上游手段。这里的每一条断言都是为了「不能把错的判成对的」——

- 列名不同 → 即使值碰巧相同也判错（`column-mismatch`），避免“答非所问”混过去；
- 行顺序不同 → 正确（多重集语义，SQL 没写 ORDER BY 时顺序无意义）；
- 数值容差 → 允许浮点误差（默认 0.01），但 `1` 与 `1.5` 必须判错；
- NULL ≠ 空串 ≠ 0（三者是不同结果，不做暗中等价）；
- 重复行必须按数量对（两组 `[A, A]` 与 `[A]` 不相等）。
"""
from __future__ import annotations

import decimal

import pytest

from evaluation.execution_accuracy import (
    accuracy_summary,
    compare_result_sets,
    normalize_column,
    normalize_value,
    to_decimal,
)


def test_exact_match_passes():
    r = compare_result_sets(
        ["区域", "销售额"], [["华东", 100], ["华南", 90]],
        ["区域", "销售额"], [["华东", 100], ["华南", 90]],
    )
    assert r.ok and r.reason == "match"


def test_row_order_is_irrelevant():
    r = compare_result_sets(
        ["区域", "销售额"], [["华东", 100], ["华南", 90]],
        ["区域", "销售额"], [["华南", 90], ["华东", 100]],
    )
    assert r.ok, "多重集语义：没写 ORDER BY 时顺序不该判错"


def test_default_mode_ignores_aliases_but_requires_same_arity():
    """BEAVER 口径：别名不同不算错（看值），但列数不同必须错。"""
    alias_diff = compare_result_sets(
        ["region", "sales"], [["华东", 100]],
        ["区域", "销售额"], [["华东", 100]],
    )
    assert alias_diff.ok, "列名只是表述，默认不参与判定"

    arity = compare_result_sets(["a", "b"], [[1, 2]], ["a"], [[1]])
    assert not arity.ok and arity.reason == "column-count"


def test_strict_mode_can_pin_column_names():
    """要钉列名时显式开 match_columns（比如列语义必须一致的分析）。"""
    diff = compare_result_sets(
        ["区域", "销售额"], [["华东", 100]],
        ["区域", "订单数"], [["华东", 100]],
        match_columns=True,
    )
    assert not diff.ok and diff.reason == "column-mismatch"


def test_numeric_tolerance_accepts_rounding_but_not_real_differences():
    ok = compare_result_sets(
        ["m"], [[decimal.Decimal("10.004")]], ["m"], [[10.0]]
    )
    assert ok.ok, "0.004 的舍入差应在默认容差 0.01 内"

    bad = compare_result_sets(["m"], [[10]], ["m"], [[10.5]])
    assert not bad.ok and bad.reason == "values"


def test_row_count_mismatch_is_its_own_reason():
    r = compare_result_sets(["a"], [[1], [2]], ["a"], [[1]])
    assert not r.ok and r.reason == "row-count"
    assert r.missing and r.extra == [], "缺失行要能指出样例行"


def test_duplicates_are_counted_as_multiset():
    r = compare_result_sets(["a"], [["x"], ["x"]], ["a"], [["x"]])
    assert not r.ok, "两组 [x,x] 与 [x] 不等"


def test_null_empty_and_zero_are_distinct():
    assert not compare_result_sets(["a"], [[None]], ["a"], [[""]]).ok
    assert not compare_result_sets(["a"], [[None]], ["a"], [[0]]).ok
    assert compare_result_sets(["a"], [[None]], ["a"], [[None]]).ok


def test_normalizers():
    assert normalize_column(" 销 售 额 ") == normalize_column("销售额")
    assert normalize_column("Region") == normalize_column("region")
    assert to_decimal("1,234.5") == decimal.Decimal("1234.5")
    assert to_decimal("华东") is None
    assert normalize_value("  华东 ") == "华东"
    assert normalize_value(None) is None


def test_accuracy_summary_only_counts_cases_with_golden():
    good = compare_result_sets(["a"], [[1]], ["a"], [[1]])
    bad = compare_result_sets(["a"], [[1]], ["a"], [[2]])
    s = accuracy_summary([good, bad, None])
    assert s["with_golden"] == 2 and s["matched"] == 1
    assert s["execution_accuracy"] == 0.5
    assert s["failure_reasons"] == {"values": 1}


def test_accuracy_summary_without_golden_reports_none():
    s = accuracy_summary([None, None])
    assert s["execution_accuracy"] is None and s["with_golden"] == 0
