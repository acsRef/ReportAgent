"""⑧ semantic evaluator 纯函数单测（Review-3，离线、无 env gate）。

钉住 _month_key / _single_measure / _money_values 的契约——Review-3 抓到
`_month_key("2024-01")` 因纯数字前缀分支先行而解析失败（ISO 分支是死代码）、
"January" 全称未真正支持；measure 提取此前依赖「金额必含小数点」外形假设。
本文件让这些纯函数回归即红。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_semantic_sql_accuracy import (  # noqa: E402
    _month_key,
    _money_values,
    _single_measure,
)


# --- _month_key：承诺格式全部支持 -------------------------------------------


@pytest.mark.parametrize(
    "v,expected",
    [
        (1, 1), (12, 12),
        ("1", 1), ("12", 12),
        ("1月", 1), ("12月", 12),
        ("2024-01", 1), ("2024-12", 12),          # Review-3：此前此处解析失败
        ("2024-01-01", 1),
        ("2024-01-01 00:00:00", 1),
        ("2024-01T00:00:00", 1),
        ("January", 1), ("january", 1),           # Review-3：全称此前未支持
        ("jan", 1), ("Jan.", 1), ("Feb 2024", 2),
        ("10月", 10),
    ],
)
def test_month_key_supported_formats(v, expected):
    assert _month_key(v) == expected


@pytest.mark.parametrize(
    "v",
    [
        "2024", "2024-13", "2024-00", "13月", "0月", "Q1", "Q4",
        "2024-13-01", "99", "-1", "last month", "", None, True,
        "2024年1月",  # 未承诺格式：宁缺勿纵
    ],
)
def test_month_key_unknown_formats_return_none(v):
    assert _month_key(v) is None


# --- _single_measure：结构驱动 measure 提取 ----------------------------------


def test_single_measure_plain_amount():
    col, val = _single_measure({"区域": "华东", "销售额": "12345.67"})
    assert col == "销售额" and val == Decimal("12345.67")


def test_single_measure_integer_amount_supported():
    """Review-3：整数金额 "100"（无小数点）是合法产物，不得 false negative。"""
    col, val = _single_measure({"月份": 1, "销售额": "100"})
    assert col == "销售额" and val == Decimal("100")


def test_single_measure_disambiguates_str_dimension_via_decimal_point():
    col, val = _single_measure({"月份": "1", "销售额": "100.5"})
    assert col == "销售额" and val == Decimal("100.5")


def test_single_measure_ambiguous_fails():
    """两个无小数点 str 数值列 → ambiguous，宁缺勿纵直接失败。"""
    with pytest.raises(AssertionError):
        _single_measure({"月份": "1", "销售额": "100"})


def test_single_measure_no_candidate_fails():
    with pytest.raises(AssertionError):
        _single_measure({"月份": 1, "区域": "华东"})


def test_money_values_sums_row_measures():
    rows = [
        {"月份": 1, "销售额": "100.5"},
        {"月份": 2, "销售额": "200"},
    ]
    assert _money_values(rows) == [Decimal("100.5"), Decimal("200")]
