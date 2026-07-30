from __future__ import annotations

import pytest

from app.infra.auth import startup_guard
from app.infra.auth.startup_guard import validate_auth_security_config

pytestmark = pytest.mark.smoke

_STRONG_SECRET = "x" * 40  # >= 32 chars, != dev literal
_STRONG_PASSWORD = "S3cure-not-default!"


@pytest.fixture
def clean_env(monkeypatch):
    """剥离所有会被启动闸读取的环境变量，保证每个用例从确定状态出发。"""
    for var in ("APP_ENV", "ALLOW_INSECURE_DEFAULT_AUTH", "JWT_SECRET", "DEFAULT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _set_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", _STRONG_SECRET)
    monkeypatch.setenv("DEFAULT_PASSWORD", _STRONG_PASSWORD)


def test_fail_closed_default_when_app_env_unset(clean_env):
    """fail-closed 基石：APP_ENV 未设置 → 按 production 处理 → 默认密钥被拒。"""
    clean_env.setenv("JWT_SECRET", startup_guard.DEV_SECRET_LITERAL)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_auth_security_config()


def test_dev_escape_allows_defaults(clean_env):
    clean_env.setenv("APP_ENV", "development")
    clean_env.setenv("ALLOW_INSECURE_DEFAULT_AUTH", "1")
    clean_env.setenv("JWT_SECRET", startup_guard.DEV_SECRET_LITERAL)
    clean_env.setenv("DEFAULT_PASSWORD", startup_guard.DEV_PASSWORD_LITERAL)
    validate_auth_security_config()  # 不抛


def test_dev_without_explicit_consent_is_rejected(clean_env):
    """开发环境但未显式开逃生门 → 仍拒绝默认密钥。"""
    clean_env.setenv("APP_ENV", "development")
    clean_env.setenv("JWT_SECRET", startup_guard.DEV_SECRET_LITERAL)
    with pytest.raises(RuntimeError):
        validate_auth_security_config()


def test_production_rejects_dev_literal_secret(clean_env):
    _set_production(clean_env)
    clean_env.setenv("JWT_SECRET", startup_guard.DEV_SECRET_LITERAL)
    with pytest.raises(RuntimeError, match="development default"):
        validate_auth_security_config()


def test_production_rejects_missing_secret(clean_env):
    _set_production(clean_env)
    clean_env.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        validate_auth_security_config()


def test_production_rejects_short_secret(clean_env):
    _set_production(clean_env)
    clean_env.setenv("JWT_SECRET", "tooshort")
    with pytest.raises(RuntimeError, match="too weak"):
        validate_auth_security_config()


def test_production_rejects_default_password(clean_env):
    _set_production(clean_env)
    clean_env.setenv("DEFAULT_PASSWORD", startup_guard.DEV_PASSWORD_LITERAL)
    with pytest.raises(RuntimeError, match="DEFAULT_PASSWORD"):
        validate_auth_security_config()


def test_production_accepts_strong_config(clean_env):
    _set_production(clean_env)
    validate_auth_security_config()  # 不抛


def test_staging_uses_production_rules(clean_env):
    _set_production(clean_env)
    clean_env.setenv("APP_ENV", "staging")
    clean_env.setenv("JWT_SECRET", startup_guard.DEV_SECRET_LITERAL)
    with pytest.raises(RuntimeError):
        validate_auth_security_config()


def test_escape_hatch_ignored_in_production(clean_env):
    """ALLOW_INSECURE_DEFAULT_AUTH 在非开发环境完全无效。"""
    clean_env.setenv("APP_ENV", "production")
    clean_env.setenv("ALLOW_INSECURE_DEFAULT_AUTH", "1")
    clean_env.setenv("JWT_SECRET", startup_guard.DEV_SECRET_LITERAL)
    with pytest.raises(RuntimeError):
        validate_auth_security_config()
