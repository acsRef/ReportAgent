"""draft-lock-release：执行结束后 _persist_report 释放 draft 锁。

修复「confirm 成功后 draft 永久 locked → 重新生成 / adjust / PATCH 全被拒」。
"""
from __future__ import annotations

import pytest

from app.agent import confirmed_execution_graph as ceg

pytestmark = pytest.mark.graphs


class _FakeConn:
    def __init__(self, log: list):
        self.log = log

    async def execute(self, sql: str, *params):
        self.log.append((sql, params))


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn=None):
        self.conn = conn or _FakeConn([])

    def acquire(self):
        return _FakeAcquire(self.conn)


class _FakeTracer:
    def end(self, status: str) -> None:
        pass


async def test_release_draft_lock_issues_complete_update(monkeypatch):
    """release_lock 把 draft 从 locked 释放回 complete（draft_id + user_id 过滤）。"""
    pool = _FakePool()
    monkeypatch.setattr(ceg, "get_pool", lambda: pool)

    await ceg._release_draft_lock({"draft_id": 5, "user_id": 3})

    assert len(pool.conn.log) == 1
    sql, params = pool.conn.log[0]
    assert "SET status = 'complete'" in sql
    assert "status = 'locked'" in sql
    assert "WHERE id = $1 AND user_id = $2" in sql
    assert params == (5, 3)


async def test_release_draft_lock_skipped_without_draft_id(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(ceg, "get_pool", lambda: pool)

    await ceg._release_draft_lock({})
    await ceg._release_draft_lock({"draft_id": None, "user_id": 3})

    assert pool.conn.log == []


async def test_persist_report_releases_lock_after_success(monkeypatch):
    """SUCCESS 落库后必须调用 release（三态一致，这里以 SUCCESS 代表）。"""
    calls: list[dict] = []

    async def fake_release(state):
        calls.append(state.get("draft_id"))

    monkeypatch.setattr(ceg, "_release_draft_lock", fake_release)
    monkeypatch.setattr(ceg, "get_tracer", lambda tid: _FakeTracer())

    async def fake_persist(**kw):
        return {"version": 2}

    monkeypatch.setattr(ceg.report_version_service, "persist_confirmed_run", fake_persist)

    state = {
        "session_id": "s1",
        "user_id": 3,
        "draft_id": 5,
        "trace_id": "t1",
        "report_payload": {"answer": {"text": "ok"}},
        "execution_status": "SUCCESS",
        "query_result": {"sql": "SELECT 1", "columns": [], "rows": []},
        "error": None,
    }
    result = await ceg._persist_report(state)

    # P11 Review-1 P1-1：_persist_report 不再覆写 execution_status；verdict 由
    # _confirmed_report_agent 写入 state 沿流到 main.py 决定 error/report SSE 出口。
    assert "execution_status" not in result
    assert result["report_payload"]["version"] == 2
    assert calls == [5]


async def test_persist_report_early_return_releases_nothing(monkeypatch):
    """payload None 的早退分支不释放：无版本行，由 lock_for_execution 恢复兜底。"""
    calls: list = []

    async def fake_release(state):
        calls.append(state.get("draft_id"))

    monkeypatch.setattr(ceg, "_release_draft_lock", fake_release)
    result = await ceg._persist_report({
        "session_id": "s1", "user_id": 3, "draft_id": 5,
        "report_payload": None, "execution_status": "FAILED",
    })
    assert result["execution_status"] == "FAILED"
    assert calls == []