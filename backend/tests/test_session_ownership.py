"""A-2 测试：/chat 入口会话归属校验（docs/plans/2026-08-04-agent-security-hardening.md）。

chat() 是 v2 + legacy 共用入口，旧实现对已存在 session 不校验 user_id——
任何人都能 resume 他人 checkpoint、往他人会话写消息（IDOR）。
PATCH requirement / confirm / retry 端点早已校验，本闸补齐 /chat。

三分支 + 端点接线：
- 会话不存在 / session_id 为空 → 放行（新会话合法创建）
- 会话存在且归属当前用户 → 放行
- 会话存在但属于他人 → 404 SESSION_NOT_FOUND（SSE 流开始前抛出）

离线可跑：monkeypatch session_manager.get_session，不触 PG；
端点用例用 dependency_overrides 替换鉴权，不跑 lifespan、不碰 LLM。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.smoke


@pytest.fixture
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> dict:
    """可控的内存会话存储，替换 session_manager.get_session。"""
    store: dict[str, dict] = {}

    async def _fake_get_session(session_id: str):
        return store.get(session_id)

    from app.main import session_manager

    monkeypatch.setattr(session_manager, "get_session", _fake_get_session)
    return store


# --- _require_session_owner 三分支 ----------------------------------------------

async def test_missing_session_passes(fake_sessions):
    """会话不存在 → 放行，chat() 随后会合法创建新会话。"""
    from app.main import _require_session_owner

    await _require_session_owner("brand-new-session", user_id=1)


async def test_empty_session_id_passes(fake_sessions):
    """session_id 为空（None / 空串）→ 放行。"""
    from app.main import _require_session_owner

    await _require_session_owner(None, user_id=1)
    await _require_session_owner("", user_id=1)


async def test_owner_match_passes(fake_sessions):
    fake_sessions["s-1"] = {"user_id": 1}

    from app.main import _require_session_owner

    await _require_session_owner("s-1", user_id=1)


async def test_foreign_session_raises_404(fake_sessions):
    """他人会话 → 404 SESSION_NOT_FOUND，与 PATCH/confirm/retry 端点一致。"""
    fake_sessions["s-1"] = {"user_id": 1}

    from app.main import _require_session_owner

    with pytest.raises(HTTPException) as exc_info:
        await _require_session_owner("s-1", user_id=2)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "SESSION_NOT_FOUND"


# --- 端点接线：404 在 SSE 流开始前抛出 -------------------------------------------

@pytest.mark.parametrize("mode", ["new", "legacy"])
async def test_chat_endpoint_rejects_foreign_session_before_streaming(fake_sessions, mode):
    """/chat 对他人会话返回标准 HTTP 404——不是 SSE 流内 error 事件。

    v2（new）与 legacy 两条路都必须在 mode 分发前被拦下。
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.infra.auth.deps import get_current_user

    fake_sessions["victim-session"] = {"user_id": 1}
    app.dependency_overrides[get_current_user] = lambda: {"id": 2, "username": "attacker"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat",
                json={"user_query": "你好", "session_id": "victim-session", "mode": mode},
            )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "SESSION_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
