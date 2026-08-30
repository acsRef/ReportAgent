"""P10 接线闭环：report_graph 产出 v2 spec 可过三层 Validator；父图 violations→FAILED。

数据真实性从「确定性工具的实现巧合」变成「被 Validator 钉住的契约」：
- 真图（monkeypatch LLM 与工具）产出的 spec 必须 ok=True；
- 注入 fabricated rows 的 spec 必须把 verdict 打成 FAILED + REPORT_VALIDATION_ERROR
（宪法 §10 永不伪造成功；用户码走 QUERY_FAILED 兜底，前端零改动）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.report.spec import ComponentSpec, DataBinding, ReportSpec, TableSpec

pytestmark = pytest.mark.graphs

QR = {
    "sql": "SELECT region, amount FROM fact_sales",
    "columns": [{"name": "region"}, {"name": "amount"}],
    "rows": [{"region": "华东", "amount": 100}, {"region": "华北", "amount": 200}],
    "row_count": 2,
    "status": "SUCCESS",
}

_STATE = {
    "query_result": QR,
    "user_query": "各区域销售额",
    "chart_config": {},
    "insight": "",
    "report_spec": None,
    "assemble_plan": [],
    "assemble_step_idx": 0,
    "assemble_results": [],
    "trace_id": "t1",
}


def test_report_graph_output_passes_validator(monkeypatch):
    """T4：真图闭环（LLM 选工具 + chart_advisor 产 chart）→ spec 带 provenance 且过三层。"""
    from app.agent import report_graph as rg
    from app.report.validator import validate_report_spec

    monkeypatch.setattr(
        rg, "call_llm",
        lambda prompt, **kw: json.dumps(
            {"steps": [{"tool": "chart_advisor", "args": {}, "description": "推荐图表"}]},
            ensure_ascii=False,
        ),
    )
    fake_chart = json.dumps({
        "type": "bar",
        "config": {"data": QR["rows"], "dimensions": {"x": "region", "y": "amount"}},
    }, ensure_ascii=False)
    monkeypatch.setattr(rg, "chart_advisor", lambda data_json: fake_chart)

    out = rg.build_report_graph().invoke(dict(_STATE))
    spec = out["report_spec"]

    assert spec.components, "bar 型 chart 应产出组件"
    assert spec.components[0].data_binding.fields == ["region", "amount"]
    assert spec.components[0].data_binding.rows == QR["rows"]
    assert spec.table is not None
    assert spec.table.columns == ["region", "amount"]
    assert validate_report_spec(spec, QR).ok is True


def _patch_confirmed_graph(monkeypatch, rs: dict):
    import app.agent.confirmed_execution_graph as ceg

    class FakeGraph:
        async def ainvoke(self, state, *a, **k):
            return rs

    monkeypatch.setattr(ceg, "build_report_graph", lambda: FakeGraph())


def _run_report_agent(**overrides) -> dict:
    from app.agent.confirmed_execution_graph import _confirmed_report_agent

    state = {
        "user_query": "各区域销售额",
        "user_id": 1,
        "session_id": "sid",
        "trace_id": "t1",
        "query_result": dict(QR),
        "report_payload": None,
        "execution_status": "",
        "error": None,
    }
    state.update(overrides)
    return asyncio.run(_confirmed_report_agent(state))


def test_fabricated_report_spec_fails_confirmation(monkeypatch):
    """T5：spec 携带 QueryResult 之外的行 → FAILED + REPORT_VALIDATION_ERROR（永不伪造成功）。"""
    decisions: list[dict] = []

    class FakeTracer:
        def add_decision(self, **fields):
            decisions.append(fields)

    monkeypatch.setattr(
        "app.agent.confirmed_execution_graph.current_tracer", lambda: FakeTracer()
    )
    bad_spec = ReportSpec(
        components=[ComponentSpec(
            id="c1", type="bar", title="数据分析",
            data_binding=DataBinding(
                fields=["region", "amount"],
                rows=[{"region": "华东", "amount": 100}, {"region": "幽灵区", "amount": 5}],
            ),
        )],
        insight="幽灵区表现最佳",  # 编造叙述
        table=TableSpec(columns=["region", "amount"]),
    )
    _patch_confirmed_graph(monkeypatch, {
        "chart_config": {"type": "bar", "config": {"data": [{"region": "幽灵区", "amount": 5}]}},
        "insight": bad_spec.insight,
        "report_spec": bad_spec,
    })

    result = _run_report_agent()

    assert result["execution_status"] == "FAILED"
    assert result["error"] is not None
    assert result["error"].code == "REPORT_VALIDATION_ERROR"
    # P8 D5 语义复用：violations 决策进 trace（可观察）
    assert decisions and decisions[0]["name"] == "report_validate"
    assert decisions[0]["action"] == "fail"
    payload = result["report_payload"]
    assert payload["answer"]["table"] is None
    assert payload["answer"]["chart"] is None
    assert payload["execution_status"] == "FAILED"
    assert payload["error"]["code"] == "REPORT_VALIDATION_ERROR"


def test_valid_report_spec_keeps_success(monkeypatch):
    """合法 spec（rows 直通 QueryResult）→ SUCCESS 路径不受影响。"""
    good_spec = ReportSpec(
        components=[ComponentSpec(
            id="c1", type="bar", title="数据分析",
            data_binding=DataBinding(
                fields=["region", "amount"],
                rows=[{"region": "华东", "amount": 100}, {"region": "华北", "amount": 200}],
            ),
        )],
        insight="华北高于华东",
        table=TableSpec(columns=["region", "amount"]),
    )
    _patch_confirmed_graph(monkeypatch, {
        "chart_config": {"type": "bar", "config": {"data": QR["rows"], "dimensions": {"x": "region", "y": "amount"}}},
        "insight": good_spec.insight,
        "report_spec": good_spec,
    })

    result = _run_report_agent()

    assert result["execution_status"] == "SUCCESS"
    assert result["error"] is None
    answer = result["report_payload"]["answer"]
    assert answer["table"]["rows"] == QR["rows"]
    assert answer["chart"]["type"] == "bar"


def test_legacy_report_state_without_spec_stays_success(monkeypatch):
    """旧形状 rs（无 report_spec 键）不进校验——兼容既有 stub / checkpoint 语义。"""
    _patch_confirmed_graph(monkeypatch, {"chart_config": None, "insight": "旧状态"})
    result = _run_report_agent()
    assert result["execution_status"] == "SUCCESS"
    assert result["error"] is None
