"""`DELETE /api/v1/sessions/{sid}` 的三态契约（plan 2026-11-11-session-hard-delete）。

端点逻辑只有三件事，全在这里钉住，不依赖 PG：

| 情况 | 期望 |
|---|---|
| 该会话有未完成的后台任务 | 409 `SESSION_BUSY`（任务还在写 report_version） |
| 会话不存在 / 他人 | 404 `SESSION_NOT_FOUND`（不泄露存在性） |
| 正常 | 200 + `{"deleted": {...}}` |
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import main
from app.infra.execution import registry
from app.services import session_deletion

pytestmark = pytest.mark.contracts

_USER = {"id": 7, "username": "tester"}


class _Task:
    def __init__(self, finished: bool) -> None:
        self.finished = finished


def test_busy_session_rejected(monkeypatch) -> None:
    """任务在跑时删会话会写出孤儿 report_version —— 必须 409，而不是硬删。"""
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: _Task(finished=False))

    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.delete_session_endpoint("s1", user=_USER))

    assert exc.value.status_code == 409
    assert "SESSION_BUSY" in str(exc.value.detail)


def test_finished_task_does_not_block(monkeypatch) -> None:
    """任务已结束（finished=True）不该拦——TTL 内它仍留在注册表里。"""
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: _Task(finished=True))

    async def fake_delete(session_id: str, *, user_id: int) -> dict[str, int]:
        return {"agent.session": 1}

    monkeypatch.setattr(session_deletion, "delete_session", fake_delete)

    import asyncio

    out = asyncio.run(main.delete_session_endpoint("s1", user=_USER))
    assert out == {"deleted": {"agent.session": 1}}


def test_missing_or_foreign_session_maps_to_404(monkeypatch) -> None:
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)

    async def boom(session_id: str, *, user_id: int) -> dict[str, int]:
        raise session_deletion.SessionNotFound(session_id)

    monkeypatch.setattr(session_deletion, "delete_session", boom)

    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.delete_session_endpoint("s1", user=_USER))
    assert exc.value.status_code == 404
    assert exc.value.detail == "SESSION_NOT_FOUND"


def test_owner_id_comes_from_jwt_not_body(monkeypatch) -> None:
    """删除用的 user_id 必须来自 JWT 上下文（函数签名里就没有 body 参数）。"""
    monkeypatch.setattr(registry, "get_confirmed_task", lambda sid: None)
    seen: dict[str, int] = {}

    async def spy(session_id: str, *, user_id: int) -> dict[str, int]:
        seen["user_id"] = user_id
        return {}

    monkeypatch.setattr(session_deletion, "delete_session", spy)

    import asyncio

    asyncio.run(main.delete_session_endpoint("s1", user=_USER))
    assert seen["user_id"] == _USER["id"]
