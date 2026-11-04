"""报告显示层：列名中文化 + 重复列去重（plan 2026-11-04-requirement-slot-contract 收尾）。

实测截图里的问题：`SELECT ... AS 周, ... AS week_of_year, ... AS 品类` ——
模型把同一列输出两次、第二次漏了中文别名，界面上就多出一列 `week_of_year`。
这里钉住：**表格标题走注册表中文映射、整列取值相同的重复列去掉、图表轴名同步改名**。
"""
from __future__ import annotations

import pytest

from app.knowledge import registry as knowledge
from app.report.labels import (
    build_display_table,
    column_label,
    drop_duplicate_columns,
    localize_chart,
)

pytestmark = pytest.mark.graphs


# --- 中文标签（注册表驱动） -------------------------------------------------


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("week_of_year", "周"),
        ("dim_date.week_of_year", "周"),
        ("category", "品类"),
        ("dim_product.category", "品类"),
        ("region", "区域"),
        ("dim_store.region", "区域"),
        ("month", "月份"),
        ("payment_amount", "实收金额"),
        ("order_amount", "销售额"),
        ("quantity", "销量"),
        # 已经是中文的别名不能被改
        ("周", "周"),
        ("品类", "品类"),
        ("销售额", "销售额"),
        # 认不出的保持原样（绝不乱猜）
        ("mystery_col", "mystery_col"),
    ],
)
def test_column_label(column: str, expected: str) -> None:
    assert column_label(column) == expected


def test_display_label_unknown_returns_none() -> None:
    assert knowledge.display_label("mystery_col") is None
    assert knowledge.display_label("") is None


# --- 重复列去重 -------------------------------------------------------------


ROWS_DUP = [
    {"周": 2, "品类": "手机", "week_of_year": 2, "销售额": "887563.06"},
    {"周": 3, "品类": "平板", "week_of_year": 3, "销售额": "786657.27"},
]


def test_drop_duplicate_columns_by_values() -> None:
    """同一表达式起了两个别名（周 / week_of_year）→ 只留第一个。"""
    kept = drop_duplicate_columns(["周", "品类", "week_of_year", "销售额"], ROWS_DUP)

    assert kept == ["周", "品类", "销售额"]


def test_drop_duplicate_keeps_single_column_result() -> None:
    assert drop_duplicate_columns(["周"], [{"周": 1}]) == ["周"]


def test_drop_duplicate_keeps_distinct_columns_with_equal_first_value() -> None:
    rows = [{"a": 1, "b": 1}, {"a": 2, "b": 3}]
    assert drop_duplicate_columns(["a", "b"], rows) == ["a", "b"]


# --- 表格构造 ---------------------------------------------------------------


def test_build_display_table_titles_chinese_keys_unchanged() -> None:
    table, label_map = build_display_table(
        ["周", "品类", "week_of_year", "销售额"], ROWS_DUP,
    )

    assert [c["key"] for c in table["columns"]] == ["周", "品类", "销售额"]
    assert [c["title"] for c in table["columns"]] == ["周", "品类", "销售额"]
    # 被去掉的重复列也进映射（指向「周」）——图表行键才能一起收敛
    assert label_map == {
        "周": "周", "品类": "品类", "销售额": "销售额", "week_of_year": "周",
    }


def test_localized_chart_has_no_english_keys_for_duplicate_columns() -> None:
    """复现实测：SQL 同时输出 `AS 周` 与 `AS week_of_year` → 图表行里不许再有英文键。"""
    table, label_map = build_display_table(
        ["周", "品类", "week_of_year", "销售额"], ROWS_DUP,
    )
    chart = {
        "type": "line",
        "config": {
            "dimensions": {"x": "week_of_year", "y": "销售额"},
            "data": [dict(row) for row in ROWS_DUP],
        },
    }

    localized = localize_chart(chart, label_map)
    row_keys = set().union(*(row.keys() for row in localized["config"]["data"]))

    assert "week_of_year" not in row_keys
    assert row_keys == {"周", "品类", "销售额"}
    assert localized["config"]["dimensions"]["x"] == "周"
    assert [c["title"] for c in table["columns"]] == ["周", "品类", "销售额"]


def test_build_display_table_translates_physical_names() -> None:
    rows = [{"week_of_year": 2, "category": "手机", "order_amount": 100}]

    table, label_map = build_display_table(
        ["week_of_year", "category", "order_amount"], rows,
    )

    assert [c["title"] for c in table["columns"]] == ["周", "品类", "销售额"]
    # key 必须是原始列名（前端按 key 取数）
    assert [c["key"] for c in table["columns"]] == ["week_of_year", "category", "order_amount"]
    assert label_map["order_amount"] == "销售额"


# --- 图表同步改名 -----------------------------------------------------------


def test_localize_chart_renames_axis_and_row_keys() -> None:
    chart = {
        "type": "line",
        "config": {
            "dimensions": {"x": "week_of_year", "y": "order_amount"},
            "data": [
                {"week_of_year": 2, "order_amount": 100},
                {"week_of_year": 3, "order_amount": 120},
            ],
        },
    }
    label_map = {"week_of_year": "周", "order_amount": "销售额"}

    localized = localize_chart(chart, label_map)

    assert localized["config"]["dimensions"] == {"x": "周", "y": "销售额"}
    assert localized["config"]["data"] == [
        {"周": 2, "销售额": 100},
        {"周": 3, "销售额": 120},
    ]


def test_localize_chart_is_noop_without_map() -> None:
    chart = {"type": "bar", "config": {"data": [{"a": 1}]}}

    assert localize_chart(chart, {}) == chart
    assert localize_chart(None, {"a": "甲"}) is None


def test_localize_chart_handles_dataset_and_explicit_fields() -> None:
    chart = {
        "type": "bar",
        "config": {"dataset": [{"category": "手机"}], "xField": "category", "yField": "order_amount"},
    }

    localized = localize_chart(chart, {"category": "品类", "order_amount": "销售额"})

    assert localized["config"]["dataset"] == [{"品类": "手机"}]
    assert localized["config"]["xField"] == "品类"
    assert localized["config"]["yField"] == "销售额"
