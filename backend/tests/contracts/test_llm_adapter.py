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


def test_to_chat_kwargs_includes_timeout(monkeypatch):
    """P6 review P1-2: LLMConfig.timeout 必须真接到 ChatOpenAI,不能是死配置。"""
    monkeypatch.setenv("LLM_TIMEOUT", "42")
    cfg = LLMConfig()
    assert cfg.timeout == 42
    kwargs = cfg.to_chat_kwargs()
    assert kwargs["timeout"] == 42


def test_to_chat_kwargs_default_timeout(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT", raising=False)
    cfg = LLMConfig()
    assert cfg.timeout == 60
    assert cfg.to_chat_kwargs()["timeout"] == 60


def test_schema_validation_error_is_distinct_subclass():
    """P6 review P1-1: parse / schema validation 异常语义必须分开。"""
    from app.llm.adapter import SchemaValidationError, StructuredParseError

    # 保持 ValueError 子类身份,旧 except ValueError 仍可兜底
    assert issubclass(SchemaValidationError, ValueError)
    assert issubclass(StructuredParseError, ValueError)
    # 但二者互不兼容(SchemaValidationError 不是 Parse,反之亦然)
    assert not issubclass(SchemaValidationError, StructuredParseError)
    assert not issubclass(StructuredParseError, SchemaValidationError)


def test_generate_structured_schema_failure_raises_schema_validation_error(monkeypatch):
    """P6 review P1-1: schema 不合法时抛 SchemaValidationError,不重试 LLM。"""
    from unittest.mock import MagicMock
    from pydantic import BaseModel, Field
    from app.llm.adapter import LLMAdapter, SchemaValidationError, StructuredParseError

    class _Schema(BaseModel):
        required_field: str = Field(...)

    # JSON 解析成功但缺少 required_field —— schema validation 应失败
    fake_resp = MagicMock()
    fake_resp.content = '{"unrelated": "x"}'
    fake_resp.usage_metadata = {}
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_resp

    monkeypatch.setattr("app.llm.adapter.ChatOpenAI", lambda **kw: fake_llm)
    monkeypatch.setattr("app.llm.adapter.invoke_with_retry", lambda op, **kw: op())
    monkeypatch.setattr("app.infra.trace.sdk.current_tracer", lambda: None)

    adapter = LLMAdapter()
    with pytest.raises(SchemaValidationError):
        adapter.generate_structured("prompt", schema=_Schema)

    # 只发生一次 LLM 调用 —— 不能因为 schema 错再发一次
    assert fake_llm.invoke.call_count == 1


def test_generate_structured_safe_does_not_retry_on_schema_failure(monkeypatch):
    """P6 review P1-1: schema validation failure 绝不能进入 _safe 的 parse fallback。"""
    from unittest.mock import MagicMock
    from pydantic import BaseModel, Field
    from app.llm.adapter import LLMAdapter, SchemaValidationError, StructuredParseError

    class _Schema(BaseModel):
        required_field: str = Field(...)

    fake_resp = MagicMock()
    fake_resp.content = '{"unrelated": "x"}'
    fake_resp.usage_metadata = {}
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_resp

    monkeypatch.setattr("app.llm.adapter.ChatOpenAI", lambda **kw: fake_llm)
    monkeypatch.setattr("app.llm.adapter.invoke_with_retry", lambda op, **kw: op())
    monkeypatch.setattr("app.infra.trace.sdk.current_tracer", lambda: None)

    adapter = LLMAdapter()

    # 不传 parser: schema 错直接抛 SchemaValidationError,不发起第二次 LLM
    with pytest.raises(SchemaValidationError):
        adapter.generate_structured_safe("prompt", schema=_Schema)
    assert fake_llm.invoke.call_count == 1

    # 即使传 parser: schema 错也仍直接抛,不会先调 parser 再发 LLM
    def _stub_parser(text: str):
        return {"also_unrelated": "y"}

    with pytest.raises(SchemaValidationError):
        adapter.generate_structured_safe(
            "prompt",
            parser=_stub_parser,
            schema=_Schema,
        )
    assert fake_llm.invoke.call_count == 2  # 第二轮独立测试多调一次


def test_generate_structured_safe_parser_fallback_on_parse_failure(monkeypatch):
    """P6 review P1-1 反向证明:parse 失败时仍正常走 parser 兜底路径。"""
    from unittest.mock import MagicMock
    from app.llm.adapter import LLMAdapter, StructuredParseError

    fake_resp = MagicMock()
    fake_resp.content = "not json at all"
    fake_resp.usage_metadata = {}
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_resp

    monkeypatch.setattr("app.llm.adapter.ChatOpenAI", lambda **kw: fake_llm)
    monkeypatch.setattr("app.llm.adapter.invoke_with_retry", lambda op, **kw: op())
    monkeypatch.setattr("app.infra.trace.sdk.current_tracer", lambda: None)

    adapter = LLMAdapter()

    # 主路径 parse 失败 → 走 parser 兜底
    def _parser(text: str):
        return {"recovered": True}

    out = adapter.generate_structured_safe("prompt", parser=_parser)
    assert out == {"recovered": True}
    # 主路径 generate 1 次 + fallback generate 1 次 = 2 次 LLM
    assert fake_llm.invoke.call_count == 2

    # 不传 parser 时,parse 失败仍抛 StructuredParseError(子类型 ValueError)
    with pytest.raises(StructuredParseError):
        adapter.generate_structured_safe("prompt")
    # 又多调用了 2 次(主路径 + 兜底 generate),共 4 次
    assert fake_llm.invoke.call_count == 4


def test_llm_adapter_generate_records_input_output_to_tracer(monkeypatch):
    """LLMAdapter.generate() → Tracer.add_llm_call → LLMCall.input/output 链路。

    P13 Review 锁住：adapter → tracer 的 redact(prompt) / redact(strip_think_tags(content)) 透传，
    防未来 caller 把 LLM input/output 丢掉（导致 Langfuse generation 空白）。
    """
    from unittest.mock import MagicMock
    from app.llm.adapter import LLMAdapter, strip_think_tags
    from app.observability.redaction import redact
    from app.infra.trace import sdk

    cfg = LLMConfig()
    adapter = LLMAdapter(cfg)

    # fake LLM response（含 think 标签 + 手机号 PII）
    fake_content = "<think>hidden</think>actual response about 手机 13800138000"
    fake_resp = MagicMock()
    fake_resp.content = fake_content
    fake_resp.usage_metadata = {"input_tokens": 11, "output_tokens": 22}

    # patch _chat 让 adapter 不真的连 LLM；patch invoke_with_retry 跳过重试包装
    mock_chat = MagicMock()
    mock_chat.invoke.return_value = fake_resp
    monkeypatch.setattr(adapter, "_chat", lambda **kw: mock_chat)
    monkeypatch.setattr("app.llm.adapter.invoke_with_retry", lambda fn, **kw: fn())

    t = sdk.Tracer(trace_id="t-adapter")
    token = sdk._current_tracer.set(t)
    try:
        prompt = "Query about 13800138000"
        text_out = adapter.generate(prompt)
    finally:
        sdk._current_tracer.reset(token)

    # adapter 仍返回原文（接口契约不变）
    assert text_out  # strip_think_tags 后的实际响应
    # 关键契约：LLMCall.input/output 都已 redact 落 tracer
    assert t._llm_calls, "adapter.generate 应记 llm_call"
    lc = t._llm_calls[-1]
    assert lc.input is not None and lc.output is not None
    # PII 已 redact（手机号必须被 mask）
    assert "13800138000" not in str(lc.input)
    assert "13800138000" not in str(lc.output)
    # think 标签已 strip
    assert "hidden" not in str(lc.output)
    # 精确等于：adapter 用 redact(prompt)/redact(text) 写入
    assert lc.input == redact(prompt)
    assert lc.output == redact(strip_think_tags(fake_content))
