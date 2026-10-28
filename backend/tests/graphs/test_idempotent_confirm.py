"""B7 幂等闸：同一 draft 已执行完成 → 重放，不重复执行。

边界（docs/testing-boundaries.md · B7）：**同一份确认需求，只能被执行一次**。
重复点确认 / 客户端超时重发 / `/retry` 都不得再跑一遍 SQL，也不得再产出新版本。

三条不变量：
1. 已有 `status='done'` 版本 → 重放它（不执行、不落库）；
2. FAILED（`status='error'`）→ 允许重试（否则一次失败永久锁死）；
3. `mode=adjust`（base_report_version 非 None）→ 每次都该产新版本，不参与幂等。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.graphs

_STATE = {
    "user_id": 1,
    "session_id": "sess-idem",
    "trace_id": "t",
    "draft_id": 7,
    "base_report_version": None,
    "requirement_card": None,
    "execution_status": "",
    "error": None,
}


class _FakeConn:  # noqa: D401 - 只用于 acquire() 的形状
    pass


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()

    def acquire(self):
        return _FakeAcquire(self.conn)


def _patch_pool(monkeypatch, ceg, finder):
    monkeypatch.setattr(ceg, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(ceg.report_version_repository, "find_done_by_draft", finder)


async def test_replays_existing_done_version_without_executing(monkeypatch) -> None:
    import app.agent.confirmed_execution_graph as ceg

    async def finder(conn, *, session_id, user_id, requirement_draft_id):
        assert session_id == "sess-idem"
        assert requirement_draft_id == 7
        return {
            "version": 3,
            "parent_version": None,
            "title": "报告",
            "status": "done",
            "report_payload": json.dumps(
                {"answer": {"text": "查询完成", "table": {"rows": [1]}},
                 "execution_status": "EMPTY"}
            ),
        }

    _patch_pool(monkeypatch, ceg, finder)

    out = await ceg._idempotency_guard(dict(_STATE))

    assert out["replayed_version"] == 3
    assert out["execution_status"] == "EMPTY", "verdict 必须原样重放"
    assert out["report_payload"]["version"] == 3
    assert out["report_payload"]["title"] == "报告"
    assert out["idempotency_key"] == "sess-idem:7"


async def test_executes_when_no_done_version(monkeypatch) -> None:
    import app.agent.confirmed_execution_graph as ceg

    async def finder(conn, **kwargs):
        return None

    _patch_pool(monkeypatch, ceg, finder)

    out = await ceg._idempotency_guard(dict(_STATE))

    assert out.get("replayed_version") is None
    assert "report_payload" not in out, "没有历史版本时不得伪造 payload"
    assert out["idempotency_key"] == "sess-idem:7"


async def test_adjust_mode_bypasses_idempotency_entirely(monkeypatch) -> None:
    """mode=adjust 每次都产出新版本，不能被当成重复执行重放掉。"""
    import app.agent.confirmed_execution_graph as ceg

    called = {"n": 0}

    async def finder(conn, **kwargs):
        called["n"] += 1
        return {"version": 9, "report_payload": "{}"}

    _patch_pool(monkeypatch, ceg, finder)

    state = dict(_STATE, base_report_version=2)
    out = await ceg._idempotency_guard(state)

    assert out == {}
    assert called["n"] == 0, "adjust 不该查幂等表"


def test_route_after_idempotency() -> None:
    import app.agent.confirmed_execution_graph as ceg

    assert ceg._route_after_idempotency({"replayed_version": 3}) == "replay"
    assert ceg._route_after_idempotency({"replayed_version": None}) == "execute"
    assert ceg._route_after_idempotency({}) == "execute"


async def test_graph_replay_path_skips_sql_execution(monkeypatch) -> None:
    """图级：幂等闸命中后直接 END，sql_gate 及下游一次都不许进。"""
    import app.agent.confirmed_execution_graph as ceg

    calls: list[str] = []

    async def fake_load(state):
        return {}

    async def fake_idem(state):
        return {
            "replayed_version": 5,
            "report_payload": {"answer": {"text": "replay"}, "version": 5},
            "execution_status": "SUCCESS",
        }

    async def fake_gate(state):
        calls.append("sql_gate")
        return {"execution_status": "RUNNING"}

    def fake_persist(state):
        calls.append("persist")
        return {}

    monkeypatch.setattr(ceg, "_load_confirmed_requirement", fake_load)
    monkeypatch.setattr(ceg, "_idempotency_guard", fake_idem)
    monkeypatch.setattr(ceg, "_sql_gate", fake_gate)
    monkeypatch.setattr(ceg, "_persist_report", fake_persist)

    graph = ceg.build_confirmed_execution_graph()
    result = await graph.ainvoke(
        dict(_STATE), {"configurable": {"thread_id": "idem-replay"}}
    )

    assert calls == [], "重放路径不得执行 SQL，也不得再落库"
    assert result["replayed_version"] == 5
    assert result["report_payload"]["version"] == 5
