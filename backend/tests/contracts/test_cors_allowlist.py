"""Final Hardening ⑫：CORS 显式白名单（不再 `*` + credentials）。

失败闭合与 auth 启动闸同哲学：未显式配置时 development 给 localhost:3000，
production/未设 APP_ENV 给空列表（同源 only）——绝不回落到通配符。
"""
from __future__ import annotations

import pytest

from app.main import _cors_allowed_origins, app


@pytest.fixture(autouse=True)
def _clear_cors_env(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)


def _middleware_origins():
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    for item in app.user_middleware:
        if isinstance(item, Middleware) and item.cls is CORSMiddleware:
            return item.kwargs.get("allow_origins")
    return None


def test_no_wildcard_origin_in_app_middleware():
    origins = _middleware_origins()
    assert origins is not None
    assert "*" not in origins, "allow_origins 不得含通配符（与 credentials=True 互斥）"


def test_development_default_is_localhost_3000(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    assert _cors_allowed_origins() == [
        "http://localhost:3000", "http://127.0.0.1:3000",
    ]


def test_unset_app_env_is_fail_closed_same_origin(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_DEFAULT_AUTH", raising=False)
    assert _cors_allowed_origins() == []


def test_production_default_is_same_origin_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert _cors_allowed_origins() == []


def test_explicit_env_origins_win(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://analytics.example.com, https://analytics.cn.example.com",
    )
    assert _cors_allowed_origins() == [
        "https://analytics.example.com", "https://analytics.cn.example.com",
    ]
