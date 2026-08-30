"""P10 report/versioning.py 契约：三态 → 存储状态映射（fail-closed）。

append-only 不变量由既有 tests/test_sql_error_envelope.py 三态落库路由钉 +
真 e2e（P12 手动门）覆盖；本模块钉纯函数语义。
"""
from __future__ import annotations

import pytest

from app.report.versioning import resolve_report_status


def test_success_and_empty_resolve_to_done():
    assert resolve_report_status("SUCCESS") == "done"
    assert resolve_report_status("EMPTY") == "done"


def test_failed_resolves_to_error():
    assert resolve_report_status("FAILED") == "error"


def test_unknown_status_fails_closed():
    """未知 execution_status → error（fail-closed：不伪造成功）。"""
    assert resolve_report_status("RUNNING") == "error"
    assert resolve_report_status("") == "error"
    assert resolve_report_status("weird") == "error"


def test_service_persists_resolved_status():
    """report_version_service 的 report_status 字面量已换源（消费 resolver）。"""
    import inspect

    from app.services import report_version_service

    src = inspect.getsource(report_version_service)
    assert "resolve_report_status" in src


# --- P9 Review-1 P9-1：persist_error_run 不得把 session phase 覆盖回 report_ready ---


def test_persist_error_run_targets_error_session_state():
    """persist_error_run 必须显式声明 session_phase="error" + 保留 failed_action。"""
    import asyncio

    from app.services import report_version_service as svc

    captured: list[dict] = []

    async def fake_persist(**kwargs):
        captured.append(kwargs)
        return {"version": 1}

    original = svc._persist
    svc._persist = fake_persist
    try:
        asyncio.run(svc.persist_error_run(
            session_id="s1",
            user_id=1,
            requirement_draft_id=None,
            title="报告",
            error_detail={"code": "TASK_TIMEOUT", "message": "m", "kind": "timeout"},
            query_snapshot=None,
            trace_id="t1",
            failed_action="confirm",
        ))
    finally:
        svc._persist = original

    assert captured[0]["session_phase"] == "error"
    assert captured[0]["last_failed_action"] == "confirm"


def test_persist_success_paths_default_to_report_ready():
    """SUCCESS/EMPTY 落库语义不变：默认 report_ready + 清 failed_action。"""
    import asyncio
    import inspect

    from app.services import report_version_service as svc

    captured: list[dict] = []

    async def fake_persist(**kwargs):
        captured.append(kwargs)
        return {"version": 1}

    original = svc._persist
    svc._persist = fake_persist
    try:
        asyncio.run(svc.persist_empty_run(
            session_id="s1", user_id=1, requirement_draft_id=1,
            title="报告", query_snapshot=None, trace_id="t1",
        ))
    finally:
        svc._persist = original

    # 未显式传 session_phase → _persist 默认 report_ready（现状语义）
    assert "session_phase" not in captured[0] or captured[0]["session_phase"] == "report_ready"


def test_persist_session_update_honors_error_phase():
    """真实 _persist 的 session UPDATE 参数由 session_phase 驱动（fake pool 契约钉）。"""
    import asyncio

    from app.services import report_version_service as svc

    executes: list[tuple] = []

    class FakeConn:
        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, *args):
            executes.append((sql, args))

    class FakePool:
        def acquire(self):
            return FakeConn()

    original_pool = svc.get_pool
    original_append = svc.report_version_repository.append_version

    async def fake_append(conn, **kwargs):
        return {"version": 1}

    svc.get_pool = lambda: FakePool()
    svc.report_version_repository.append_version = fake_append
    try:
        asyncio.run(svc.persist_error_run(
            session_id="s1",
            user_id=1,
            requirement_draft_id=None,
            title="报告",
            error_detail={"code": "TASK_TIMEOUT", "message": "m", "kind": "timeout"},
            query_snapshot=None,
            trace_id="t1",
            failed_action="confirm",
        ))
    finally:
        svc.get_pool = original_pool
        svc.report_version_repository.append_version = original_append

    # 最后一条 execute 是 session UPDATE：current_phase 必须是 error 且保留 failed_action
    session_sql, session_args = executes[-1]
    assert "UPDATE agent.session" in session_sql
    assert "report_ready" not in session_args
    assert "error" in session_args
    assert "confirm" in session_args
