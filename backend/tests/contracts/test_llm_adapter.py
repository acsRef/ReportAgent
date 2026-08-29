from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.llm.adapter import strip_think_tags
from app.llm.config import LLMConfig


def test_strip_think_basic():
    assert strip_think_tags("<think>hidden</think>  hello") == "hello"
    assert strip_think_tags(" <THINK> a </THINK> b ") == "b"
    assert strip_think_tags("<reasoning>r</reasoning>ok") == "ok"
    assert strip_think_tags("no tag") == "no tag"
    assert strip_think_tags("<think>multi\nline</think>  x") == "x"


def test_strip_think_keeps_json():
    raw = "<think>reason</think>  {\"a\": 1}"
    assert strip_think_tags(raw) == '{"a": 1}'


def test_settings_alias_minimax(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "mk-123")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://mini.test/v1")
    cfg = LLMConfig()
    assert cfg.api_key == "mk-123"
    assert cfg.base_url == "https://mini.test/v1"


def test_settings_llm_overrides_minimax(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")
    cfg = LLMConfig()
    assert cfg.api_key == "llm-key"


def test_context_window_default(monkeypatch):
    monkeypatch.delenv("LLM_CONTEXT_WINDOW", raising=False)
    cfg = LLMConfig()
    assert cfg.context_window == 131072


def test_adapter_generate_structured_parses_json(monkeypatch):
    from unittest.mock import MagicMock
    from app.llm.adapter import LLMAdapter

    fake_resp = MagicMock()
    fake_resp.content = '<think>r</think>  {"status": "complete", "summary": "s"}'
    fake_resp.usage_metadata = {}
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_resp
    monkeypatch.setattr("app.llm.adapter.ChatOpenAI", lambda **kw: fake_llm)
    monkeypatch.setattr("app.llm.adapter.invoke_with_retry", lambda op, **kw: op())
    monkeypatch.setattr("app.infra.trace.sdk.current_tracer", lambda: None)

    adapter = LLMAdapter()
    out = adapter.generate_structured("prompt")
    assert out == {"status": "complete", "summary": "s"}


def test_adapter_generate_strips_think(monkeypatch):
    from unittest.mock import MagicMock
    from app.llm.adapter import LLMAdapter

    fake_resp = MagicMock()
    fake_resp.content = "<think>hidden</think>  answer"
    fake_resp.usage_metadata = {}
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_resp
    monkeypatch.setattr("app.llm.adapter.ChatOpenAI", lambda **kw: fake_llm)
    monkeypatch.setattr("app.llm.adapter.invoke_with_retry", lambda op, **kw: op())
    monkeypatch.setattr("app.infra.trace.sdk.current_tracer", lambda: None)

    adapter = LLMAdapter()
    assert adapter.generate("prompt") == "answer"
