from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.contracts

from app.infra.trace.models import LLMCall, Span
from app.observability.langfuse_flush import flush_to_langfuse

# langfuse_config mock：enabled=True + flush_timeout 数值（wait_for 需要真实 float）。
_CFG = SimpleNamespace(enabled=True, flush_timeout=5.0)


def _make_tracer(trace_id: str = "t-1") -> MagicMock:
    """构造 Tracer 形状的 mock（真实 Span/LLMCall 数据类 + 含 PII 的 user_query）。"""
    tracer = MagicMock()
    tracer.trace_id = trace_id
    tracer.session_id = "s-1"
    tracer.user_id = 1
    tracer.user_query = "查询 id_card 110101199001011234 的销售"  # 含 PII
    tracer._spans = [
        Span(
            trace_id=trace_id,
            span_id="sp-1",
            span_name="intent",
            input={"q": "id_card 110101199001011234"},
        )
    ]
    tracer._llm_calls = [
        LLMCall(span_id="sp-1", model="deepseek-reasoner", prompt_tokens=100, completion_tokens=50, latency_ms=1500),
    ]
    tracer._prompt_versions = [
        {"span_id": "sp-1", "name": "intent_classify", "version": 1},
    ]
    tracer._decisions = [
        {"span_id": "sp-1", "name": "diagnose_route", "reason": "sql repair route"},
    ]
    return tracer


def _mock_langfuse() -> MagicMock:
    """返回带 root context-manager 契约的 mock client。"""
    mock_langfuse = MagicMock()
    mock_root = MagicMock()
    mock_root.__enter__.return_value = mock_root
    mock_root.__exit__.return_value = False
    mock_langfuse.start_as_current_observation.return_value = mock_root
    return mock_langfuse


@pytest.mark.asyncio
async def test_flush_to_langfuse_writes_spans_and_metadata(monkeypatch):
    """flush_to_langfuse 把 trace 转 Langfuse root observation + flush。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)

    assert mock_langfuse.start_as_current_observation.called
    # 第一次调用是根 observation；call_args 只回最后一次调用（子 span）。
    root_call = mock_langfuse.start_as_current_observation.call_args_list[0].kwargs
    assert root_call.get("name") == "report_agent_run"
    assert root_call.get("trace_context") == {"trace_id": "t-1"}  # trace_id 与 PG 一致
    assert mock_langfuse.flush.called


@pytest.mark.asyncio
async def test_flush_to_langfuse_skips_when_disabled(monkeypatch):
    """enabled=False → 直接 return，不 touch client。"""
    spy = MagicMock()
    monkeypatch.setattr("app.observability.langfuse_flush.get_langfuse_client", spy)
    await flush_to_langfuse(
        _make_tracer(),
        langfuse_config=SimpleNamespace(enabled=False),
    )
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_flush_to_langfuse_skips_when_client_none(monkeypatch):
    """Langfuse client=None（env 未设）→ 直接 return，不抛。"""
    monkeypatch.setattr("app.observability.langfuse_flush.get_langfuse_client", lambda: None)
    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)


@pytest.mark.asyncio
async def test_flush_to_langfuse_redacts_pii_in_inputs(monkeypatch):
    """flush_to_langfuse 入参前 PII mask（user_query / span input 含 id_card 应被 mask）。"""
    mock_langfuse = MagicMock()
    mock_root = MagicMock()
    mock_root.__enter__.return_value = mock_root
    mock_root.__exit__.return_value = False

    captured = []

    def capture_observation(*, name, trace_context=None, input=None, **kwargs):
        captured.append(f"{name}:{input}")
        return mock_root

    mock_langfuse.start_as_current_observation.side_effect = capture_observation
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)

    flat = "|".join(str(x) for x in captured)
    assert "110101199001011234" not in flat  # 任何 observation input 都不含 PII


@pytest.mark.asyncio
async def test_flush_to_langfuse_writes_decisions_metadata(monkeypatch):
    """P8 D5 Diagnose 决策写入 Langfuse observation metadata（code comment 承诺 P13 落库）。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)

    update_calls = mock_langfuse.update_current_span.call_args_list
    assert update_calls
    metas = [c.kwargs.get("metadata") for c in update_calls]
    assert any(isinstance(m, dict) and m.get("decision") for m in metas)


@pytest.mark.asyncio
async def test_flush_to_langfuse_transform_uuid_trace_id(monkeypatch):
    """UUID（带连字符）trace_id → Langfuse 要求的 32 位小写 hex；raw UUID 落 pg_trace_id metadata。

    真测试复现：Langfuse v4 OTel 内核拒收非 32-lowercase-hex trace id
    （"invalid literal for int() with base 16"），带连字符的 UUID 必须转 hex。
    """
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )
    tracer = _make_tracer(trace_id="31a08ab3-05dc-4d5e-aaa9-656ed891edd8")

    await flush_to_langfuse(tracer, langfuse_config=_CFG)

    root_call = mock_langfuse.start_as_current_observation.call_args_list[0].kwargs
    assert root_call["trace_context"] == {"trace_id": "31a08ab305dc4d5eaaa9656ed891edd8"}
    assert root_call["metadata"] == {"pg_trace_id": "31a08ab3-05dc-4d5e-aaa9-656ed891edd8"}


@pytest.mark.asyncio
async def test_flush_to_langfuse_handles_exception(monkeypatch):
    """Langfuse SDK 抛异常 → 主流程不抛（best-effort）。"""
    mock_langfuse = MagicMock()
    mock_langfuse.start_as_current_observation.side_effect = RuntimeError("network down")
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)  # 不抛