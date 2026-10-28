"""Tests for _confirmed_report_agent.

Verifies that answer.table is built from query_result, and that
execution_status reflects the three-state verdict:

- query_result absent           → FAILED
- query_result.error is set     → FAILED
- query_result present, no err, no rows → EMPTY  (was FAILED before)
- query_result present, rows    → SUCCESS
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs


def test_report_agent_with_query_result() -> None:
    """有 query_result 时 → answer.table 含 columns/rows, SUCCESS."""
    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    import asyncio

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight": "销售额增长12%"}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        coro = _confirmed_report_agent({
            "user_query": "销售趋势",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": {
                "sql": "SELECT ...",
                "columns": [{"name": "region", "type": "text"}],
                "rows": [{"region": "华东", "total": 100}],
                "row_count": 1,
                "status": "SUCCESS",
            },
            "report_payload": None,
            "execution_status": "",
            "error": None,
        })
        result = asyncio.run(coro)
    finally:
        ceg.build_report_graph = original

    payload = result.get("report_payload", {})
    answer = payload.get("answer", {})
    assert answer.get("table") is not None
    assert "columns" in answer["table"]
    assert "rows" in answer["table"]
    assert answer["table"]["rows"] == [{"region": "华东", "total": 100}]
    assert result["execution_status"] == "SUCCESS"


def test_report_agent_empty_result() -> None:
    """空 query_result → execution_status=EMPTY, table=None.

    Was FAILED before; the bug was that legitimate "no match" results
    were indistinguishable from real query failures.
    """
    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    import asyncio

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight": ""}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        coro = _confirmed_report_agent({
            "user_query": "销售趋势",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": {
                "sql": "SELECT ...",
                "columns": [{"name": "region", "type": "text"}],
                "rows": [],
                "row_count": 0,
                "status": "SUCCESS",
            },
            "report_payload": None,
            "execution_status": "",
            "error": None,
        })
        result = asyncio.run(coro)
    finally:
        ceg.build_report_graph = original

    payload = result.get("report_payload", {})
    answer = payload.get("answer", {})
    assert answer.get("table") is None
    assert result["execution_status"] == "EMPTY"
    assert payload["execution_status"] == "EMPTY"
    # 空结果话术：EMPTY 时 insight 写入「未找到匹配数据 + 放宽筛选条件」引导，
    # 随 report SSE 下发并落入 report_version 历史，让用户知道这不是查询失败。
    assert answer.get("insight") is not None
    assert "未找到匹配数据" in answer["insight"]
    assert "放宽筛选条件" in answer["insight"]


def test_report_agent_no_result() -> None:
    """query_result 完全不存在 → execution_status=FAILED.

    Distinct from EMPTY because there was no SQL result at all — the
    sub-graph never produced one (likely a hard error before execute).
    """
    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    import asyncio

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight": ""}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        coro = _confirmed_report_agent({
            "user_query": "销售趋势",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": None,
            "report_payload": None,
            "execution_status": "",
            "error": None,
        })
        result = asyncio.run(coro)
    finally:
        ceg.build_report_graph = original

    assert result["execution_status"] == "FAILED"

def test_no_query_result_preserves_upstream_error_kind() -> None:
    """qr=None 时不能把上游的 error_kind 丢掉（2026-10-28 实测缺陷）。

    场景：模型凭空写 JOIN dim_supplier → EXPLAIN 报 object_not_found →
    diagnose 走 clarify/fail 直接 END，不走 build_output → query_result=None。
    此前这里只从 qr 取 error，于是用户看到笼统的「查询执行失败,请稍后重试」
    而不是「表/字段不存在」，也无法按 kind 渲染 ErrorCard。
    """
    import asyncio

    from app.agent.confirmed_execution_graph import _confirmed_report_agent
    from app.models.contracts import ErrorDetail

    def _stub_report_graph():
        class FakeGraph:
            async def ainvoke(self, state, *a, **k):
                return {"chart_config": None, "insight": ""}
        return FakeGraph()

    import app.agent.confirmed_execution_graph as ceg
    original = ceg.build_report_graph
    ceg.build_report_graph = _stub_report_graph
    try:
        result = asyncio.run(_confirmed_report_agent({
            "user_query": "2024年各供应商的采购金额",
            "user_id": 1,
            "session_id": "sid",
            "trace_id": "t",
            "query_result": None,
            "error": ErrorDetail(
                code="EXECUTION_ERROR",
                message='relation "dim_supplier" does not exist',
                kind="object_not_found",
            ),
        }))
    finally:
        ceg.build_report_graph = original

    assert result["execution_status"] == "FAILED"
    assert result["error"] is not None, "上游 error 必须被保留"
    assert result["error"].kind == "object_not_found", "kind 决定用户看到的错误文案"
    assert "dim_supplier" in result["report_payload"]["error"]["message"]
