"""Graph-level routing tests for the confirmed execution graph.

All three verdicts (SUCCESS / EMPTY / FAILED) now persist a row so
version history shows the full timeline. The previous behaviour was
to skip persist for FAILED/EMPTY, which meant failed attempts and
zero-match runs had no historical trace and the front-end had no way
to distinguish them from each other or from a real success.

SSE still uses the verdict to decide whether to emit an `error` event
(in `main.py`); persistence now happens regardless so users can
inspect old failed/empty attempts from the right-rail version list.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.graphs

_BASE_STATE = {
    "user_id": 1,
    "user_query": "",
    "session_id": "routing-test",
    "trace_id": "t",
    "requirement_card": None,
    "base_report_version": None,
    "adjustment_text": None,
    "schema_context": None,
    "query_result": None,
    "report_payload": None,
    "execution_status": "",
    "error": None,
}


class _FakeReportGraph:
    async def ainvoke(self, state, *a, **k):
        return {"chart_config": None, "insight": ""}


def _patch_upstream(monkeypatch, ceg, query_result):
    """Stub every node upstream of report_agent; record persist calls."""
    calls: list[str] = []

    async def fake_load(state):
        return {}

    async def fake_gate(state):
        return {"execution_status": "RUNNING"}

    async def fake_data(state):
        return {"schema_context": None}

    async def fake_sql(state):
        return {"query_result": query_result, "execution_status": "FAILED"}

    async def fake_persist(state):
        calls.append("persist")
        return {
            "execution_status": "DONE",
            "report_payload": {**(state.get("report_payload") or {}), "version": 1},
        }

    monkeypatch.setattr(ceg, "_load_confirmed_requirement", fake_load)
    monkeypatch.setattr(ceg, "_sql_gate", fake_gate)
    monkeypatch.setattr(ceg, "_confirmed_data_agent", fake_data)
    monkeypatch.setattr(ceg, "_confirmed_sql_agent", fake_sql)
    monkeypatch.setattr(ceg, "_persist_report", fake_persist)
    monkeypatch.setattr(ceg, "build_report_graph", lambda: _FakeReportGraph())
    return calls


async def test_failed_report_persists_with_error_status(monkeypatch) -> None:
    """query_result=None → report_agent FAILED → still persists (status='error').

    The SSE error event is emitted by main.py separately; the persist
    step only writes a status='error' row so the user can find the
    failed attempt later. The final execution_status in the graph is
    DONE because persist_report succeeded.
    """
    import app.agent.confirmed_execution_graph as ceg

    calls = _patch_upstream(monkeypatch, ceg, query_result=None)
    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_BASE_STATE), {"configurable": {"thread_id": "routing-fail"}}
    )

    assert calls == ["persist"], (
        "FAILED runs must still persist (status='error') so users can "
        "see what they tried in version history"
    )
    assert result["execution_status"] == "DONE"


async def test_empty_rows_report_persists_with_empty_status(monkeypatch) -> None:
    """Zero rows but no error → EMPTY → still persists (status='done')."""
    import app.agent.confirmed_execution_graph as ceg

    calls = _patch_upstream(
        monkeypatch,
        ceg,
        query_result={
            "sql": "SELECT 1 WHERE FALSE",
            "columns": [{"name": "x"}],
            "rows": [],
            "row_count": 0,
        },
    )
    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_BASE_STATE), {"configurable": {"thread_id": "routing-empty"}}
    )

    assert calls == ["persist"], (
        "EMPTY runs must still persist (status='done', payload "
        "execution_status=EMPTY) so the front-end can render the "
        "no-data band instead of pretending the report was empty"
    )
    assert result["execution_status"] == "DONE"
    assert result["report_payload"]["execution_status"] == "EMPTY"


async def test_success_report_persists(monkeypatch) -> None:
    """Rows present → SUCCESS → persist_report runs → DONE."""
    import app.agent.confirmed_execution_graph as ceg

    calls = _patch_upstream(
        monkeypatch,
        ceg,
        query_result={
            "sql": "SELECT region, SUM(total) FROM fact_sales GROUP BY region",
            "columns": [{"name": "region"}, {"name": "total"}],
            "rows": [{"region": "华东", "total": 100}],
            "row_count": 1,
        },
    )
    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_BASE_STATE), {"configurable": {"thread_id": "routing-ok"}}
    )

    assert calls == ["persist"], "a successful report must be persisted"
    assert result["execution_status"] == "DONE"
    assert result["report_payload"]["answer"]["table"]["rows"] == [
        {"region": "华东", "total": 100}
    ]


# --- P11：子图 callbacks 窄转发 + report 事件 wire 形态 ------------------------------


def test_callbacks_only_forwards_narrow_config() -> None:
    """只透传 callbacks——thread_id 等 configurable 不进无 checkpointer 子图。"""
    import app.agent.confirmed_execution_graph as ceg

    class _H:
        pass

    h = _H()
    assert ceg._callbacks_only(None) is None
    assert ceg._callbacks_only({}) is None
    assert ceg._callbacks_only({"configurable": {"thread_id": "t"}}) is None
    assert ceg._callbacks_only({"callbacks": [h]}) == {"callbacks": [h]}


async def test_persist_report_merges_wire_fields(monkeypatch) -> None:
    """P11 F3：merged report_payload 带 version/parent_version/title（sse-v2 wire 形态）。

    P11 Review-1 P1-1：verdict（SUCCESS / EMPTY / FAILED）由 _confirmed_report_agent
    写入 state，_persist_report 不覆写——main.py 据此决定 error/report SSE 出口。
    本测试断言返回值不含 execution_status 键（避免再次钉住 P1-1 bug）。
    """
    import app.agent.confirmed_execution_graph as ceg

    captured: dict = {}

    async def fake_persist_confirmed_run(**kwargs):
        captured.update(kwargs)
        return {"version": 3, "parent_version": None, "title": "报告"}

    async def fake_draft_id(state):
        return 7

    async def fake_release(state):
        return None

    class _FakeTracer:
        def end(self, status: str) -> None:
            pass

    monkeypatch.setattr(
        ceg.report_version_service, "persist_confirmed_run", fake_persist_confirmed_run
    )
    monkeypatch.setattr(ceg, "_draft_id_from_state", fake_draft_id)
    monkeypatch.setattr(ceg, "_release_draft_lock", fake_release)
    monkeypatch.setattr(ceg, "get_tracer", lambda *a, **kw: _FakeTracer())

    state = {
        **_BASE_STATE,
        "report_payload": {"answer": {"text": "ok"}},
        "execution_status": "SUCCESS",
        "draft_id": 7,
    }
    out = await ceg._persist_report(state)

    # verdict 不被覆盖（P11 Review-1 P1-1 修复）
    assert "execution_status" not in out
    merged = out["report_payload"]
    assert merged["version"] == 3
    assert merged["parent_version"] is None
    assert merged["title"] == "报告"
    assert captured["requirement_draft_id"] == 7
