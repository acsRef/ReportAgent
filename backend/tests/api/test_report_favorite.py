"""报告版本收藏路由（2026-11-02 工具栏接线）。

`agent.report_version.favorite` 自 P10 起就有列、有仓储函数 `set_favorite`，
却一直没有路由和 caller——前端「收藏」按钮只弹了个 toast。这里钉住新路由的
最小契约：

1. 置位/取消都透传到仓储，且 user_id 取自 JWT（不是请求体）；
2. 仓储返回 False（版本不存在 / 不属于该用户）→ 404 VERSION_NOT_FOUND，
   不区分两者（避免版本枚举）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.main import FavoriteRequest, set_report_favorite

pytestmark = pytest.mark.api


class _Acquire:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Pool:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self._conn)


def _patch_pool(monkeypatch) -> None:
    monkeypatch.setattr("app.infra.db.postgres.get_pool", lambda: _Pool(object()))


def _patch_repo(monkeypatch, *, result: bool) -> list[dict]:
    calls: list[dict] = []

    async def _fake_set_favorite(conn, **kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(
        "app.main.report_version_repository.set_favorite", _fake_set_favorite,
    )
    return calls


async def test_favorite_true_persists_with_jwt_user(monkeypatch):
    _patch_pool(monkeypatch)
    calls = _patch_repo(monkeypatch, result=True)

    resp = await set_report_favorite(
        "sid-1", 3, FavoriteRequest(favorite=True), {"id": 7},
    )

    assert resp == {"version": 3, "favorite": True}
    assert calls == [
        {"session_id": "sid-1", "user_id": 7, "version": 3, "favorite": True},
    ]


async def test_unfavorite_round_trip(monkeypatch):
    _patch_pool(monkeypatch)
    calls = _patch_repo(monkeypatch, result=True)

    resp = await set_report_favorite(
        "sid-1", 3, FavoriteRequest(favorite=False), {"id": 7},
    )

    assert resp == {"version": 3, "favorite": False}
    assert calls[0]["favorite"] is False


async def test_missing_or_foreign_version_is_404(monkeypatch):
    _patch_pool(monkeypatch)
    _patch_repo(monkeypatch, result=False)

    with pytest.raises(HTTPException) as exc:
        await set_report_favorite(
            "someone-elses", 9, FavoriteRequest(favorite=True), {"id": 7},
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "VERSION_NOT_FOUND"
