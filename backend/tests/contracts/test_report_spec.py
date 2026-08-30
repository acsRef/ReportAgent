"""P10 report/spec.py 契约：ReportSpec v2（KpiSpec/TableSpec/DataBinding provenance）。

向后兼容钉：旧落库 payload 形状（data_binding 自由 dict / 缺省）必须仍可 parse；
contracts shim 只 re-export 不复制（P9 llm_resilience shim 先例）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.report.spec import ComponentSpec, DataBinding, KpiSpec, ReportSpec, TableSpec


def test_v2_spec_roundtrip():
    spec = ReportSpec(
        version="1.0",
        components=[ComponentSpec(
            id="c1", type="bar", title="数据分析",
            data_binding=DataBinding(
                fields=["region", "amount"],
                rows=[{"region": "华东", "amount": 100}, {"region": "华北", "amount": 200}],
            ),
        )],
        insight="华东最高",
        kpi=[KpiSpec(label="总销售额", field="amount", aggregation="sum", value=300)],
        table=TableSpec(columns=["region", "amount"]),
    )
    dumped = spec.model_dump()
    reparse = ReportSpec.model_validate(dumped)
    assert reparse.kpi[0].value == 300
    assert reparse.table.columns == ["region", "amount"]
    assert reparse.components[0].data_binding.fields == ["region", "amount"]
    assert reparse.components[0].data_binding.rows[1]["amount"] == 200


def test_legacy_payload_still_parses():
    """旧形状（无 kpi/table、data_binding 为自由 dict 或缺省）必须仍可 parse。"""
    legacy = {
        "version": "1.0",
        "components": [
            {"id": "c1", "type": "bar", "title": "数据分析", "visual_config": {"x": 1}},
            {"id": "c2", "type": "table", "title": "明细", "data_binding": {}},
        ],
        "insight": "旧报告",
    }
    spec = ReportSpec.model_validate(legacy)
    assert spec.insight == "旧报告"
    assert spec.kpi == []
    assert spec.table is None
    # data_binding 自由 dict → DataBinding 默认值归一
    assert spec.components[0].data_binding.source == "query_result"
    assert spec.components[0].data_binding.fields == []
    assert spec.components[1].data_binding.rows == []


def test_contracts_shim_reexports_same_objects():
    """contracts.py 只 re-export：ReportSpec/ComponentSpec 与 report.spec 同一对象。"""
    from app.models import contracts

    assert contracts.ReportSpec is ReportSpec
    assert contracts.ComponentSpec is ComponentSpec


def test_kpi_spec_defaults_and_aggregation_enum():
    kpi = KpiSpec(label="总销售额", field="amount")
    assert kpi.aggregation == "sum"
    assert kpi.value is None
    with pytest.raises(ValidationError):
        KpiSpec(label="x", field="amount", aggregation="median")


def test_data_binding_source_is_frozen_to_query_result():
    """provenance 锚点唯一：source 只允许 query_result（禁止自由来源）。"""
    with pytest.raises(ValidationError):
        DataBinding(source="imagination", fields=["x"])
