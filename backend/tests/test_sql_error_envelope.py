"""SSE error envelope + three-state verdict tests.

These cover the new `_build_sse_error` helper in `app.main` and the
SUCCESS / EMPTY / FAILED verdict logic in `confirmed_execution_graph`.
The helper exists so a single timeout / connection / permission /
syntax / object error is never silently collapsed into "查询未返回数据".
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.smoke


# --- _build_sse_error ----------------------------------------------------


def test_build_sse_error_includes_kind_and_sql():
    from app.main import _build_sse_error

    frame = _build_sse_error(
        {"code": "EXECUTION_ERROR", "message": "cancel due to statement timeout",
         "kind": "timeout"},
        "SELECT 1 FROM fact_sales",
        "sql",
    )
    assert frame["event"] == "error"
    payload = json.loads(frame["data"])
    assert payload["code"] == "QUERY_TIMEOUT"
    assert payload["kind"] == "timeout"
    assert payload["failed_action"] == "sql"
    assert payload["sql"] == "SELECT 1 FROM fact_sales"
    assert "查询超时" in payload["message"]
    assert "SELECT 1 FROM fact_sales" in payload["message"]
    assert payload["recoverable"] is True


def test_build_sse_error_truncates_sql_to_200():
    from app.main import _build_sse_error

    long_sql = "SELECT " + ", ".join(f"col_{i}" for i in range(500))
    frame = _build_sse_error(
        {"code": "EXECUTION_ERROR", "message": "boom", "kind": "syntax"},
        long_sql,
        "sql",
    )
    payload = json.loads(frame["data"])
    # 200-char cap + ellipsis ⇒ max 200 chars in the snippet field.
    assert len(payload["sql"]) <= 201
    assert payload["sql"].endswith("…")
    assert payload["code"] == "QUERY_SYNTAX"


def test_build_sse_error_permission_is_not_recoverable():
    from app.main import _build_sse_error

    frame = _build_sse_error(
        {"code": "EXECUTION_ERROR", "message": "permission denied", "kind": "permission"},
        "SELECT * FROM secret",
        "sql",
    )
    payload = json.loads(frame["data"])
    assert payload["code"] == "QUERY_PERMISSION"
    assert payload["recoverable"] is False


def test_build_sse_error_unknown_kind_falls_back_to_other():
    from app.main import _build_sse_error

    frame = _build_sse_error(
        {"code": "X", "message": "weird", "kind": "bogus"},
        None,
        "sql",
    )
    payload = json.loads(frame["data"])
    assert payload["code"] == "QUERY_FAILED"
    assert payload["kind"] == "other"
    # No SQL → message stays as the friendly fallback, no "尝试的 SQL:" suffix
    assert "尝试的 SQL" not in payload["message"]
    assert payload["sql"] == ""


def test_build_sse_error_collapses_newlines_in_sql():
    from app.main import _build_sse_error

    sql = "SELECT *\n  FROM fact_sales\n WHERE  year = 2024"
    frame = _build_sse_error(
        {"code": "X", "message": "x", "kind": "object"},
        sql,
        "sql",
    )
    payload = json.loads(frame["data"])
    # Multi-line SQL flattened for one-line display
    assert "\n" not in payload["sql"]
    assert payload["sql"].startswith("SELECT * FROM fact_sales")


# --- three-state verdict -------------------------------------------------


def _make_qr_dict(rows, err=None):
    return {
        "sql": "SELECT 1",
        "columns": [{"name": "x", "type": "int"}] if rows else [],
        "rows": rows,
        "row_count": len(rows),
        "status": "SUCCESS" if err is None else "FAILED",
        "error": err,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_confirmed_report_agent_empty_rows_yields_empty_status():
    """Legitimate zero-match is execution_status=EMPTY (not FAILED).

    Without this split the SSE error path fires for non-errors, which
    was the original "成功的零行被错当作失败" bug.
    """
    from app.agent import confirmed_execution_graph as ceg

    state = {
        "user_query": "anything",
        "session_id": "s1",
        "user_id": 1,
        "trace_id": "t1",
        "query_result": _make_qr_dict([]),  # SUCCESS path but zero rows
    }
    # Patch the inner report_graph to short-circuit (no LLM call).
    fake_report = AsyncMock()
    fake_report.ainvoke = AsyncMock(return_value={
        "chart_config": {}, "insight_text": "", "report_spec": None,
    })
    with patch.object(ceg, "build_report_graph", return_value=fake_report):
        result = await ceg._confirmed_report_agent(state)

    assert result["execution_status"] == "EMPTY"
    payload = result["report_payload"]
    assert payload["answer"]["table"] is None
    assert payload["execution_status"] == "EMPTY"


@pytest.mark.asyncio
async def test_confirmed_report_agent_error_yields_failed_status():
    from app.agent import confirmed_execution_graph as ceg

    state = {
        "user_query": "anything",
        "session_id": "s1",
        "user_id": 1,
        "trace_id": "t1",
        "query_result": _make_qr_dict(
            [], err={"code": "EXECUTION_ERROR",
                     "message": "cancel due to statement timeout",
                     "kind": "timeout"},
        ),
    }
    fake_report = AsyncMock()
    fake_report.ainvoke = AsyncMock(return_value={
        "chart_config": {}, "insight_text": "", "report_spec": None,
    })
    with patch.object(ceg, "build_report_graph", return_value=fake_report):
        result = await ceg._confirmed_report_agent(state)

    assert result["execution_status"] == "FAILED"
    payload = result["report_payload"]
    assert payload["execution_status"] == "FAILED"
    assert payload["error"]["kind"] == "timeout"
    # err is re-hydrated to ErrorDetail (not raw dict)
    assert result["error"] is not None
    assert result["error"].kind == "timeout"


@pytest.mark.asyncio
async def test_confirmed_report_agent_success_with_rows_yields_success():
    from app.agent import confirmed_execution_graph as ceg

    state = {
        "user_query": "anything",
        "session_id": "s1",
        "user_id": 1,
        "trace_id": "t1",
        "query_result": _make_qr_dict(
            [{"region": "华东", "amount": 100.0}],
        ),
    }
    fake_report = AsyncMock()
    fake_report.ainvoke = AsyncMock(return_value={
        "chart_config": {}, "insight_text": "hi", "report_spec": None,
    })
    with patch.object(ceg, "build_report_graph", return_value=fake_report):
        result = await ceg._confirmed_report_agent(state)

    assert result["execution_status"] == "SUCCESS"
    assert result["error"] is None
    payload = result["report_payload"]
    assert payload["answer"]["table"] is not None
    assert payload["answer"]["table"]["rows"] == [{"region": "华东", "amount": 100.0}]


# --- persistence routing -------------------------------------------------


def test_query_snapshot_for_failure_keeps_sql_and_error_kind():
    from app.agent import confirmed_execution_graph as ceg
    from app.models.contracts import ErrorDetail

    qr = {
        "sql": "SELECT * FROM fact_sales",
        "columns": [],
        "rows": [],
        "row_count": 0,
        "error": {"code": "EXECUTION_ERROR",
                  "message": "permission denied for table fact_sales",
                  "kind": "permission"},
    }
    snap = ceg._build_query_snapshot(qr, "FAILED", ErrorDetail(
        code="EXECUTION_ERROR",
        message="permission denied for table fact_sales",
        kind="permission",
    ))
    assert snap["sql"] == "SELECT * FROM fact_sales"
    assert snap["error_kind"] == "permission"
    assert "permission denied" in snap["error"]
    assert snap["rows"] == []
    assert snap["row_count"] == 0
    assert snap["truncated"] is False


def test_query_snapshot_for_empty_keeps_rows_metadata():
    from app.agent import confirmed_execution_graph as ceg

    qr = {
        "sql": "SELECT 1",
        "columns": [{"name": "x", "type": "int"}],
        "rows": [],
        "row_count": 0,
        "truncated": False,
    }
    snap = ceg._build_query_snapshot(qr, "EMPTY", None)
    assert snap["columns"] == [{"name": "x", "type": "int"}]
    assert snap["row_count"] == 0


def test_persist_report_routes_failed_to_persist_error_run():
    """FAILED verdict must call persist_error_run (status='error')."""
    from app.agent import confirmed_execution_graph as ceg
    from app.models.contracts import ErrorDetail

    state = {
        "session_id": "s1",
        "user_id": 1,
        "report_payload": {"answer": {}, "execution_status": "FAILED"},
        "execution_status": "FAILED",
        "query_result": {"sql": "SELECT 1", "columns": [], "rows": [], "row_count": 0},
        "error": ErrorDetail(code="EXECUTION_ERROR", message="timeout", kind="timeout"),
        "trace_id": "t1",
    }
    # _draft_id_from_state is a sync wrapper that returns a coroutine;
    # mock it as an AsyncMock so `await _draft_id_from_state(state)` resolves.
    with patch.object(ceg, "_draft_id_from_state",
                      new=AsyncMock(return_value=42)), \
         patch.object(ceg.report_version_service, "persist_error_run",
                      new=AsyncMock(return_value={"version": 7})) as err_run, \
         patch.object(ceg.report_version_service, "persist_empty_run",
                      new=AsyncMock()) as empty_run, \
         patch.object(ceg.report_version_service, "persist_confirmed_run",
                      new=AsyncMock()) as conf_run, \
         patch.object(ceg, "_release_draft_lock", new=AsyncMock()), \
         patch.object(ceg, "get_tracer"):
        asyncio.run(ceg._persist_report(state))

    err_run.assert_awaited_once()
    args = err_run.call_args.kwargs
    assert args["session_id"] == "s1"
    assert args["error_detail"]["kind"] == "timeout"
    empty_run.assert_not_called()
    conf_run.assert_not_called()


def test_persist_report_routes_empty_to_persist_empty_run():
    from app.agent import confirmed_execution_graph as ceg

    state = {
        "session_id": "s1",
        "user_id": 1,
        "report_payload": {"answer": {}, "execution_status": "EMPTY"},
        "execution_status": "EMPTY",
        "query_result": {"sql": "SELECT 1", "columns": [], "rows": [], "row_count": 0},
        "trace_id": "t1",
    }
    with patch.object(ceg, "_draft_id_from_state",
                      new=AsyncMock(return_value=42)), \
         patch.object(ceg.report_version_service, "persist_empty_run",
                      new=AsyncMock(return_value={"version": 8})) as empty_run, \
         patch.object(ceg.report_version_service, "persist_error_run",
                      new=AsyncMock()) as err_run, \
         patch.object(ceg.report_version_service, "persist_confirmed_run",
                      new=AsyncMock()) as conf_run, \
         patch.object(ceg, "_release_draft_lock", new=AsyncMock()), \
         patch.object(ceg, "get_tracer"):
        asyncio.run(ceg._persist_report(state))

    empty_run.assert_awaited_once()
    err_run.assert_not_called()
    conf_run.assert_not_called()


def test_persist_report_routes_success_to_persist_confirmed_run():
    from app.agent import confirmed_execution_graph as ceg

    state = {
        "session_id": "s1",
        "user_id": 1,
        "report_payload": {"answer": {"text": "x"}, "execution_status": "SUCCESS"},
        "execution_status": "SUCCESS",
        "query_result": {"sql": "SELECT 1", "columns": [], "rows": [{"x": 1}],
                          "row_count": 1},
        "trace_id": "t1",
    }
    with patch.object(ceg, "_draft_id_from_state",
                      new=AsyncMock(return_value=42)), \
         patch.object(ceg.report_version_service, "persist_confirmed_run",
                      new=AsyncMock(return_value={"version": 9})) as conf_run, \
         patch.object(ceg.report_version_service, "persist_empty_run",
                      new=AsyncMock()) as empty_run, \
         patch.object(ceg.report_version_service, "persist_error_run",
                      new=AsyncMock()) as err_run, \
         patch.object(ceg, "_release_draft_lock", new=AsyncMock()), \
         patch.object(ceg, "get_tracer"):
        asyncio.run(ceg._persist_report(state))

    conf_run.assert_awaited_once()
    empty_run.assert_not_called()
    err_run.assert_not_called()