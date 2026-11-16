"""`POST /api/v1/sessions/{sid}/compact` 的三态契约（手动压缩）。

| 情况 | 期望 |
|---|---|
| 会话不存在 / 他人 | 404 `SESSION_NOT_FOUND`（不泄露存在性） |
| 该会话有未完成的后台任务 | 409 `SESSION_BUSY`（压缩改 digest，执行中的任务正在读它） |
| 正常 | 200 + `compact_session_now` 的观测值（`reason` 说明为什么没压） |
| 压缩内部炸了 | 503 `COMPACTION_FAILED`（维护动作失败不该 500 打挂界面） |
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app import main
from app.infra.checkpoint.session import session_manager
from app.infra.execution import registry
from app.memory import conversation as conversation_memory

pytestmark = pytest.mark.contracts

_USER = {"id": 7, "username": "tester"}
_SESSION = {"thread_id": "s1", "user_id": _USER["id"]}


class _Task:
    def __init__(self, finished: bool) -> None:
        self.finished = finished


def _patch_session(monkeypatch, session: dict | None) -> None:
    async def fake_get(session_id: str):
        return session

    monkeypatch.setattr(session_manager, "get_session", fake_get)


def test_missing_session_is_404(monkeypatch) -> None:
    _patch_session(monkeypatch, None)
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert exc.value.status_code == 404
    assert exc.value.detail == "SESSION_NOT_FOUND"


def test_foreign_session_is_404(monkeypatch) -> None:
    _patch_session(monkeypatch, {"thread_id": "s1", "user_id": 999})
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert exc.value.status_code == 404


def test_running_task_is_409(monkeypatch) -> None:
    _patch_session(monkeypatch, _SESSION)
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: _Task(finished=False))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert exc.value.status_code == 409
    assert "SESSION_BUSY" in str(exc.value.detail)


def test_finished_task_does_not_block(monkeypatch) -> None:
    _patch_session(monkeypatch, _SESSION)
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: _Task(finished=True))

    async def fake_compact(session_id: str, user_id: int) -> dict:
        return {"compacted": True, "reason": None, "digest_msg_count": 21,
                "digest_version": 1, "context_chars": 500, "raw_chars": 1200}

    monkeypatch.setattr(conversation_memory, "compact_session_now", fake_compact)

    out = asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert out["compacted"] is True
    assert out["digest_msg_count"] == 21


def test_reason_passthrough_when_not_worth_it(monkeypatch) -> None:
    """不适合压缩时也要 200，并把原因说清楚（不假装成功）。"""
    _patch_session(monkeypatch, _SESSION)
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)

    async def not_worth(session_id: str, user_id: int) -> dict:
        return {"compacted": False, "reason": "NOT_WORTH_IT", "digest_msg_count": 0,
                "digest_version": 0, "context_chars": 210, "raw_chars": 220}

    monkeypatch.setattr(conversation_memory, "compact_session_now", not_worth)

    out = asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert out == {
        "compacted": False, "reason": "NOT_WORTH_IT", "digest_msg_count": 0,
        "digest_version": 0, "context_chars": 210, "raw_chars": 220,
    }


def test_internal_failure_maps_to_503(monkeypatch) -> None:
    _patch_session(monkeypatch, _SESSION)
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)

    async def boom(session_id: str, user_id: int) -> dict:
        raise RuntimeError("llm down")

    monkeypatch.setattr(conversation_memory, "compact_session_now", boom)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert exc.value.status_code == 503
    assert "COMPACTION_FAILED" in str(exc.value.detail)


def test_owner_id_comes_from_jwt_not_body(monkeypatch) -> None:
    _patch_session(monkeypatch, _SESSION)
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)
    seen: dict[str, int] = {}

    async def spy(session_id: str, user_id: int) -> dict:
        seen["user_id"] = user_id
        return {"compacted": False, "reason": "EMPTY_SESSION", "digest_msg_count": 0,
                "digest_version": 0, "context_chars": 0, "raw_chars": 0}

    monkeypatch.setattr(conversation_memory, "compact_session_now", spy)

    asyncio.run(main.compact_session_endpoint("s1", user=_USER))
    assert seen["user_id"] == _USER["id"]
