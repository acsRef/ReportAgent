"""P-4: confirmed-execution 锁定与执行的 draft 一致性测试。

load 阶段把 draft_id 写进 state；_draft_id_from_state 只读 state（不再中途
重查最新 draft），保证 gate 锁定与 persist 落库用的是同一份 draft。
"""
from __future__ import annotations

import pytest

from app.agent import confirmed_execution_graph as ceg

pytestmark = pytest.mark.graphs


async def test_draft_id_from_state_reads_state():
    assert await ceg._draft_id_from_state({"draft_id": 7}) == 7


async def test_draft_id_from_state_falls_back_to_zero():
    assert await ceg._draft_id_from_state({}) == 0
    assert await ceg._draft_id_from_state({"draft_id": None}) == 0


async def test_load_confirmed_requirement_sets_draft_id(monkeypatch):
    """load 阶段必须把 draft id 写进 state，供 gate/persist 复用。"""
    draft_row = {
        "id": 3,
        "status": "complete",
        "payload": {
            "id": "draft-x",
            "status": "complete",
            "summary": "测试需求",
            "missing_fields": [],
            "assumptions": [],
        },
    }

    class _FakeAcquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    class _FakePool:
        def acquire(self):
            return _FakeAcquire()

    monkeypatch.setattr(ceg, "get_pool", lambda: _FakePool())

    async def _fake_get_latest(conn, *, session_id, user_id):
        return draft_row

    monkeypatch.setattr(ceg.requirement_repository, "get_latest", _fake_get_latest)

    result = await ceg._load_confirmed_requirement(
        {"session_id": "s1", "user_id": 1, "trace_id": ""}
    )
    assert result["draft_id"] == 3
    assert result["requirement_card"].id == "draft-x"


async def test_load_confirmed_requirement_locked_reload_also_sets_draft_id(monkeypatch):
    """已 locked 的幂等重载分支同样要带上 draft_id。"""
    draft_row = {
        "id": 9,
        "status": "locked",
        "payload": {
            "id": "draft-y",
            "status": "locked",
            "summary": "已确认",
            "missing_fields": [],
            "assumptions": [],
            "confirmed_at": "2026-08-01T00:00:00",
        },
    }

    class _FakeAcquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    class _FakePool:
        def acquire(self):
            return _FakeAcquire()

    monkeypatch.setattr(ceg, "get_pool", lambda: _FakePool())

    async def _fake_get_latest(conn, *, session_id, user_id):
        return draft_row

    monkeypatch.setattr(ceg.requirement_repository, "get_latest", _fake_get_latest)

    result = await ceg._load_confirmed_requirement(
        {"session_id": "s2", "user_id": 1, "trace_id": ""}
    )
    assert result["draft_id"] == 9
