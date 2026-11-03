"""表格里的常量列要隐藏（2026-11-03）。

实测：「分析2024年华东销售趋势」的表格带一列 `地区=华东`——scope 已经把它过滤成
常量。它不携带信息、占一列宽度，还会让图表误以为存在分类维度。
"""
from __future__ import annotations

import pytest

from app.agent.confirmed_execution_graph import _drop_constant_columns

pytestmark = pytest.mark.contracts


def _cols(*names: str) -> list[dict]:
    return [{"key": n, "title": n} for n in names]


def test_drops_constant_column():
    cols = _cols("月份", "地区", "销售额")
    rows = [
        {"月份": 7, "地区": "华东", "销售额": "1"},
        {"月份": 8, "地区": "华东", "销售额": "2"},
    ]
    assert [c["key"] for c in _drop_constant_columns(cols, rows)] == ["月份", "销售额"]


def test_keeps_changing_column():
    cols = _cols("月份", "地区")
    rows = [{"月份": 7, "地区": "华东"}, {"月份": 8, "地区": "华南"}]
    assert len(_drop_constant_columns(cols, rows)) == 2


def test_single_row_keeps_everything():
    cols = _cols("月份", "地区")
    rows = [{"月份": 7, "地区": "华东"}]
    assert len(_drop_constant_columns(cols, rows)) == 2


def test_all_constant_does_not_empty_the_table():
    cols = _cols("地区", "渠道")
    rows = [{"地区": "华东", "渠道": "线上"}, {"地区": "华东", "渠道": "线上"}]
    assert len(_drop_constant_columns(cols, rows)) == 2


def test_single_column_result_is_kept():
    cols = _cols("合计")
    rows = [{"合计": 1}, {"合计": 1}]
    assert len(_drop_constant_columns(cols, rows)) == 1
