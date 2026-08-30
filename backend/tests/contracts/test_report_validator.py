"""P10 report/validator.py 契约：三层 Validator（结构 / 数值 / 禁止自由生成）。

校验对象是 ReportSpec → QueryResult 映射（report-runtime.md §二）；
不做文本正则数字审计（伞形 §十二：误报海）。
EMPTY / qr=None 短路 ok=True——三态中只有 SUCCESS 进三层。
"""
from __future__ import annotations

import pytest

from app.report.spec import ComponentSpec, DataBinding, KpiSpec, ReportSpec, TableSpec
from app.report.validator import validate_report_spec


QR = {
    "sql": "SELECT region, amount FROM t",
    "columns": [{"name": "region"}, {"name": "amount"}],
    "rows": [
        {"region": "华东", "amount": 100},
        {"region": "华北", "amount": 200},
    ],
    "row_count": 2,
    "status": "SUCCESS",
}


def _spec(**kw) -> ReportSpec:
    base = dict(
        components=[ComponentSpec(
            id="c1", type="bar", title="数据分析",
            data_binding=DataBinding(
                fields=["region", "amount"],
                rows=[{"region": "华东", "amount": 100}, {"region": "华北", "amount": 200}],
            ),
        )],
        insight="ok",
        table=TableSpec(columns=["region", "amount"]),
    )
    base.update(kw)
    return ReportSpec(**base)


# --- 短路 -------------------------------------------------------------------


def test_short_circuit_on_empty_or_missing_query_result():
    assert validate_report_spec(_spec(), None).ok is True
    empty = {**QR, "rows": [], "row_count": 0}
    assert validate_report_spec(_spec(), empty).ok is True


# --- 合法路径 ---------------------------------------------------------------


def test_valid_spec_passes_all_layers():
    result = validate_report_spec(_spec(), QR)
    assert result.ok is True
    assert result.violations == []


def test_valid_spec_with_kpi_passes():
    spec = _spec(kpi=[KpiSpec(label="总销售额", field="amount", aggregation="sum", value=300)])
    assert validate_report_spec(spec, QR).ok is True


# --- 结构层 -----------------------------------------------------------------


def test_structure_kpi_field_not_in_columns():
    spec = _spec(kpi=[KpiSpec(label="x", field="nope", value=1)])
    result = validate_report_spec(spec, QR)
    assert result.ok is False
    v = [x for x in result.violations if x.layer == "structure"]
    assert v and v[0].block == "kpi[0]" and v[0].field == "nope"


def test_structure_table_columns_out_of_query_result():
    spec = _spec(table=TableSpec(columns=["region", "ghost"]))
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "structure" and v.block == "table" and v.field == "ghost"
               for v in result.violations)


def test_structure_empty_table_columns_rejected():
    spec = _spec(table=TableSpec(columns=[]))
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "structure" and v.block == "table" for v in result.violations)


def test_structure_chart_fields_not_in_columns():
    spec = _spec(components=[ComponentSpec(
        id="c1", type="pie", title="x",
        data_binding=DataBinding(fields=["region", "ghost_col"], rows=[]),
    )])
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "structure" and v.field == "ghost_col" for v in result.violations)


def test_structure_chart_without_fields_rejected():
    """data-bearing 组件必须声明字段锚点（无 provenance 即结构违规）。"""
    spec = _spec(components=[ComponentSpec(
        id="c1", type="bar", title="x", data_binding=DataBinding(fields=[], rows=[]),
    )])
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "structure" and v.block == "components[0]" for v in result.violations)


def test_structure_row_keys_outside_declared_fields_rejected():
    """P10-1：row 键超出 data_binding.fields 声明 → 结构违规（provenance 闭合）。

    值即使在 QueryResult 中存在，未声明的字段也不得进渲染数据。
    """
    spec = _spec(components=[ComponentSpec(
        id="c1", type="bar", title="x",
        data_binding=DataBinding(
            fields=["region", "amount"],
            rows=[{"region": "华东", "amount": 100, "secret_flag": True}],
        ),
    )])
    result = validate_report_spec(spec, QR)
    assert result.ok is False
    assert any(
        v.layer == "structure" and v.block == "components[0]" and v.field == "secret_flag"
        for v in result.violations
    )


def test_structure_empty_row_rejected():
    """空行 {} 未携带任何锚定字段 → 无效投影，拒绝（all() 恒真不能穿透 fabrication）。"""
    spec = _spec(components=[ComponentSpec(
        id="c1", type="bar", title="x",
        data_binding=DataBinding(fields=["region", "amount"], rows=[{}]),
    )])
    result = validate_report_spec(spec, QR)
    assert result.ok is False
    assert any(v.layer == "structure" and v.block == "components[0]" for v in result.violations)


# --- 数值层 -----------------------------------------------------------------


def test_numeric_kpi_value_mismatch():
    spec = _spec(kpi=[KpiSpec(label="总销售额", field="amount", aggregation="sum", value=999)])
    result = validate_report_spec(spec, QR)
    assert result.ok is False
    assert any(v.layer == "numeric" and v.block == "kpi[0]" for v in result.violations)


def test_numeric_kpi_value_none_is_violation():
    """声明了 KPI 却没填重算值 → numeric violation（机制面钉住）。"""
    spec = _spec(kpi=[KpiSpec(label="总销售额", field="amount")])
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "numeric" and v.block == "kpi[0]" for v in result.violations)


def test_numeric_kpi_aggregation_variants():
    rows = [{"v": 10}, {"v": 20}, {"v": 30}]
    qr = {"sql": "s", "columns": [{"name": "v"}], "rows": rows, "row_count": 3, "status": "SUCCESS"}
    for agg, expect in (("avg", 20.0), ("min", 10.0), ("max", 30.0), ("count", 3.0)):
        spec = ReportSpec(
            components=[],
            kpi=[KpiSpec(label="k", field="v", aggregation=agg, value=expect)],
        )
        assert validate_report_spec(spec, qr).ok is True, agg


def test_numeric_kpi_non_numeric_column():
    spec = _spec(kpi=[KpiSpec(label="x", field="region", aggregation="sum", value=1)])
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "numeric" and v.block == "kpi[0]" for v in result.violations)


def test_deformed_row_is_fabrication_violation():
    """值组合在 QueryResult 中不存在（数值被改/维度编造）→ fabrication。

    DataBinding 无字段语义，「维度编造」与「数值变形」不可区分且本质相同：
    渲染的行数据不存在于 QueryResult（「不生成不存在的数据」）。
    """
    spec = _spec(components=[ComponentSpec(
        id="c1", type="bar", title="x",
        data_binding=DataBinding(
            fields=["region", "amount"],
            rows=[{"region": "华东", "amount": 100}, {"region": "华北", "amount": 999}],
        ),
    )])
    result = validate_report_spec(spec, QR)
    assert result.ok is False
    assert any(v.layer == "fabrication" for v in result.violations)


# --- 禁止自由生成层 ---------------------------------------------------------


def test_fabrication_row_not_in_query_result():
    """整行在 QueryResult 中不存在 → fabrication（不生成不存在的数据）。"""
    spec = _spec(components=[ComponentSpec(
        id="c1", type="bar", title="x",
        data_binding=DataBinding(
            fields=["region", "amount"],
            rows=[{"region": "华东", "amount": 100}, {"region": "华南", "amount": 5}],
        ),
    )])
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "fabrication" for v in result.violations)


def test_fabrication_row_inflation_rejected():
    """复制引用膨胀行数（> QueryResult 行数）→ fabrication。"""
    dup = {"region": "华东", "amount": 100}
    spec = _spec(components=[ComponentSpec(
        id="c1", type="bar", title="x",
        data_binding=DataBinding(fields=["region", "amount"], rows=[dup, dup, dup]),
    )])
    result = validate_report_spec(spec, QR)
    assert any(v.layer == "fabrication" for v in result.violations)


# --- 投影引用合法 -----------------------------------------------------------


def test_projected_row_reference_allowed():
    """行是 QueryResult 行的子集投影（只取锚定字段）→ 合法。"""
    spec = _spec(components=[ComponentSpec(
        id="c1", type="pie", title="x",
        data_binding=DataBinding(fields=["region"], rows=[{"region": "华东"}]),
    )])
    result = validate_report_spec(spec, QR)
    assert result.ok is True
