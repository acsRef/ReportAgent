"""Final Hardening ③：报表工具对 numeric 字符串行的容忍 + Decimal 精确汇总。

背景：execute_sql 的 numeric 列经 JSON transport 变成精确字符串（如
"123456789012345678.91"）。chart_advisor / insight_analyst / report_tools
（trend/group/detect）是纯本地计算工具——若继续用裸 isinstance((int, float))
识别数值列，金额列会被当成非数值 → 图表降级 table、摘要为空。
本文件钉住：字符串数值列要能被识别、被精确求和、被格式化。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.smoke


def _result(rows, columns=None):
    cols = columns or [
        {"name": "region", "type": "text"},
        {"name": "amount", "type": "numeric"},
    ]
    return json.dumps({"columns": cols, "rows": rows}, ensure_ascii=False)


def test_chart_advisor_classifies_numeric_string_column_as_numeric():
    from app.tools.sql_tools import chart_advisor

    out = json.loads(chart_advisor(_result([
        {"region": "华东", "amount": "15998.00"},
        {"region": "华北", "amount": "19947.15"},
    ])))
    assert out["type"] == "pie"
    assert out["config"]["dimensions"]["value"] == "amount"


def test_chart_advisor_bar_when_many_numeric_string_rows():
    from app.tools.sql_tools import chart_advisor

    rows = [{"region": f"r{i}", "amount": f"{i * 100}.50"} for i in range(12)]
    out = json.loads(chart_advisor(_result(rows)))
    assert out["type"] == "bar"
    assert out["config"]["dimensions"]["y"] == "amount"


def test_insight_analyst_sums_numeric_strings_exactly():
    from app.tools.sql_tools import insight_analyst

    text = insight_analyst(_result([
        {"region": "华东", "amount": "0.10"},
        {"region": "华北", "amount": "0.20"},
    ]))
    assert "合计=0.30" in text, text  # float 求和会得 0.30000000000000004


def test_insight_analyst_large_decimal_not_float_degraded():
    from app.tools.sql_tools import insight_analyst

    text = insight_analyst(_result([
        {"region": "a", "amount": "123456789012345678.91"},
        {"region": "b", "amount": "123456789012345678.91"},
    ]))
    assert "合计=246,913,578,024,691,357.82" in text, text


def test_group_compare_sums_numeric_strings_exactly():
    from app.tools.report_tools import group_compare

    text = group_compare(_result([
        {"region": "华东", "amount": "0.10"},
        {"region": "华北", "amount": "0.20"},
        {"region": "华东", "amount": "0.20"},
    ]))
    lines = dict(line.split(": ", 1) for line in text.splitlines())
    assert lines["华东"] == "合计=0.30"
    assert lines["华北"] == "合计=0.20"
    # 排名：华东(0.30) 在华北(0.20) 前
    assert text.splitlines()[0].startswith("华东")


def test_trend_and_detect_accept_numeric_strings():
    from app.tools.report_tools import detect_anomaly, trend_analysis

    trend = trend_analysis(json.dumps({"rows": [
        {"m": "2024-01", "v": "100.00"},
        {"m": "2024-02", "v": "110.00"},
        {"m": "2024-03", "v": "130.00"},
    ]}))
    assert "上升" in trend

    det = detect_anomaly(json.dumps({"rows": [
        {"region": f"r{i}", "v": "10"} for i in range(1, 10)
    ] + [{"region": "r-outlier", "v": "10000"}]}))
    assert "r-outlier" in det
