"""跨进程 ragent-py token 共享缓存测试。

离线——用临时缓存文件，不打真实 ragent-py。
"""
from __future__ import annotations

import time

import pytest

from app.tools import ragent_token_cache
from app.tools import interface_dict_tools

pytestmark = pytest.mark.smoke


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ragent_token_cache, "_PATH", str(tmp_path / "tokens.json"))
    ragent_token_cache.invalidate("http://x")  # 清空
    ragent_token_cache.invalidate("http://a")
    return str(tmp_path / "tokens.json")


def test_set_get_roundtrip(cache_path):
    ragent_token_cache.set_token("http://x", "tok-1")
    assert ragent_token_cache.get_token("http://x") == "tok-1"


def test_expired_returns_none(cache_path, monkeypatch):
    ragent_token_cache.set_token("http://x", "tok-1")
    monkeypatch.setattr(ragent_token_cache, "_TTL", -1)  # TTL 负 → 立即过期
    # 重新 set 会覆盖；直接伪造过期时间
    import json
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"http://x": {"token": "tok-1", "expires_at": time.time() - 10}}, f)
    assert ragent_token_cache.get_token("http://x") is None


def test_invalidate_clears(cache_path):
    ragent_token_cache.set_token("http://x", "tok-1")
    ragent_token_cache.invalidate("http://x")
    assert ragent_token_cache.get_token("http://x") is None


def test_interface_dict_login_uses_shared_cache_without_http(monkeypatch, cache_path):
    """共享缓存命中 → _login_token 不调 ragent-py 登录。"""
    ragent_token_cache.set_token("http://x", "shared-token")
    monkeypatch.setattr(interface_dict_tools, "_token_cache", {})
    monkeypatch.setattr(interface_dict_tools, "_TOKEN_LOCK", __import__("threading").Lock())
    called = {"count": 0}

    def _fake_post(*a, **k):
        called["count"] += 1
        raise AssertionError("不应调用登录")

    monkeypatch.setattr(interface_dict_tools.httpx, "post", _fake_post)
    token = interface_dict_tools._login_token("http://x")
    assert token == "shared-token"
    assert called["count"] == 0  # 未打登录接口


def test_interface_dict_login_misses_then_logs_and_caches(monkeypatch, cache_path):
    """无缓存 → 登录并写共享缓存。"""
    monkeypatch.setattr(interface_dict_tools, "_token_cache", {})
    monkeypatch.setattr(interface_dict_tools, "_TOKEN_LOCK", __import__("threading").Lock())

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "fresh-token"}

    monkeypatch.setattr(interface_dict_tools.httpx, "post", lambda *a, **k: _Resp())
    token = interface_dict_tools._login_token("http://x")
    assert token == "fresh-token"
    assert ragent_token_cache.get_token("http://x") == "fresh-token"