from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.observability.langfuse_config import LangfuseConfig


def test_langfuse_disabled_when_no_keys(monkeypatch):
    """LANGFUSE_PUBLIC_KEY / SECRET_KEY 缺一 → enabled=False。"""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    cfg = LangfuseConfig()
    assert cfg.enabled is False
    assert cfg.public_key is None


def test_langfuse_enabled_when_both_keys_set(monkeypatch):
    """LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY 都设 → enabled=True。"""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    cfg = LangfuseConfig()
    assert cfg.enabled is True
    assert cfg.public_key == "pk-lf-test"
    assert cfg.secret_key == "sk-lf-test"


def test_langfuse_host_default_cloud(monkeypatch):
    """LANGFUSE_HOST 未设 → 默认 cloud.langfuse.com。"""
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    cfg = LangfuseConfig()
    assert cfg.host == "https://cloud.langfuse.com"