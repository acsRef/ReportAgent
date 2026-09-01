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
    """P8 D5 Diagnose 决策按 span_id 写入对应 Langfuse observation metadata（code comment 承诺 P13 落库）。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)

    # _make_tracer 的 decision span_id="sp-1" 对应 span "intent" → decisions 落到该 span metadata
    span_calls = [
        c for c in mock_langfuse.start_as_current_observation.call_args_list
        if c.kwargs.get("name") == "intent"
    ]
    assert span_calls, "decision 应挂在 span observation metadata 而非 root"
    md = span_calls[0].kwargs.get("metadata") or {}
    assert "decisions" in md
    assert any(d.get("name") == "diagnose_route" for d in md["decisions"])


@pytest.mark.asyncio
async def test_flush_llm_call_includes_input_output(monkeypatch):
    """LLMCall.input/output 透传到 Langfuse generation observation（debug 必须能重建 prompt/response）。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    from app.infra.trace.models import Span as SpanModel

    tracer = MagicMock()
    tracer.trace_id = "t-io"
    tracer.session_id = "s-io"
    tracer.user_id = 1
    tracer.user_query = "q"
    tracer._spans = [SpanModel(trace_id="t-io", span_id="sp-y", span_name="intent", input=None)]
    tracer._llm_calls = [
        LLMCall(
            span_id="sp-y", model="x", prompt_tokens=10, completion_tokens=20, latency_ms=100,
            input="hello world", output="hi there",
        )
    ]
    tracer._prompt_versions = []
    tracer._decisions = []

    await flush_to_langfuse(tracer, langfuse_config=_CFG)

    llm_kwargs = [
        c.kwargs for c in mock_langfuse.start_as_current_observation.call_args_list
        if c.kwargs.get("name") == "llm_call"
    ][0]
    # redact 对无 PII 文本是 identity
    assert llm_kwargs.get("input") == "hello world"
    assert llm_kwargs.get("output") == "hi there"


@pytest.mark.asyncio
async def test_flush_llm_call_redacts_pii_in_input_output(monkeypatch):
    """LLM prompt/response 中的 PII（手机/身份证）经 redact 后才进 Langfuse（PII sink coverage 全）。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    from app.infra.trace.models import Span as SpanModel

    tracer = MagicMock()
    tracer.trace_id = "t-iop"
    tracer.session_id = "s-iop"
    tracer.user_id = 1
    tracer.user_query = "q"
    tracer._spans = [SpanModel(trace_id="t-iop", span_id="sp-y", span_name="intent", input=None)]
    tracer._llm_calls = [
        LLMCall(
            span_id="sp-y", model="x", prompt_tokens=0, completion_tokens=0, latency_ms=100,
            input="Phone 13800138000", output="id_card 110101199001011234",
        )
    ]
    tracer._prompt_versions = []
    tracer._decisions = []

    await flush_to_langfuse(tracer, langfuse_config=_CFG)

    llm_kwargs = [
        c.kwargs for c in mock_langfuse.start_as_current_observation.call_args_list
        if c.kwargs.get("name") == "llm_call"
    ][0]
    assert "13800138000" not in str(llm_kwargs.get("input"))
    assert "110101199001011234" not in str(llm_kwargs.get("output"))


@pytest.mark.asyncio
async def test_flush_llm_call_attached_under_parent_span(monkeypatch):
    """LLMCall.span_id 决定 Langfuse parent-child：generation 嵌套于对应 span observation。

    P13 Review P1：原实现把 llm_call 平铺在 root 下，丢掉 Agent→LLM causal attribution。
    """
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    await flush_to_langfuse(_make_tracer(), langfuse_config=_CFG)

    names = [c.kwargs.get("name") for c in mock_langfuse.start_as_current_observation.call_args_list]
    # 顺序：report_agent_run (root) → intent (span) → llm_call (nested)
    assert names[0] == "report_agent_run"
    assert names.index("llm_call") > names.index("intent"), (
        f"llm_call 应嵌套于 span 'intent' 之后，但调用顺序是 {names}"
    )

    # latency_ms 必须落 Langfuse metadata（P13 验收：latency 可分析）
    llm_kwargs = [
        c.kwargs for c in mock_langfuse.start_as_current_observation.call_args_list
        if c.kwargs.get("name") == "llm_call"
    ][0]
    assert llm_kwargs.get("metadata", {}).get("latency_ms") == 1500


@pytest.mark.asyncio
async def test_flush_multiple_decisions_aggregated_and_redacted(monkeypatch):
    """同一 span 下多个 decision 聚合为 list + PII redact 防覆盖与脱敏漏。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    from app.infra.trace.models import Span as SpanModel

    tracer = MagicMock()
    tracer.trace_id = "t-agg"
    tracer.session_id = "s-agg"
    tracer.user_id = 1
    tracer.user_query = "查 13800138000 的订单"
    tracer._spans = [SpanModel(trace_id="t-agg", span_id="sp-x", span_name="sql_agent", input=None)]
    tracer._llm_calls = []
    tracer._prompt_versions = []
    tracer._decisions = [
        {"span_id": "sp-x", "name": "sql_retry", "reason": "手机 13800138000"},
        {"span_id": "sp-x", "name": "sql_repair", "reason": "电话 13900139000"},
    ]

    await flush_to_langfuse(tracer, langfuse_config=_CFG)

    span_calls = [
        c for c in mock_langfuse.start_as_current_observation.call_args_list
        if c.kwargs.get("name") == "sql_agent"
    ]
    assert span_calls
    md = span_calls[0].kwargs.get("metadata") or {}
    decisions = md.get("decisions", [])
    assert len(decisions) == 2, "两个 decision 必须都保留（不能覆盖）"
    joined = str(decisions)
    assert "13800138000" not in joined, "decision PII 必须 redact"
    assert "13900139000" not in joined


@pytest.mark.asyncio
async def test_flush_unmatched_decisions_and_pvs_to_root(monkeypatch):
    """无 span_id 的 decision / prompt_version → 聚合写到 root metadata（via update_current_span）。"""
    mock_langfuse = _mock_langfuse()
    monkeypatch.setattr(
        "app.observability.langfuse_flush.get_langfuse_client",
        lambda: mock_langfuse,
    )

    tracer = MagicMock()
    tracer.trace_id = "t-orph"
    tracer.session_id = "s-orph"
    tracer.user_id = 1
    tracer.user_query = None
    tracer._spans = []
    tracer._llm_calls = []
    tracer._prompt_versions = [{"span_id": "", "name": "orphan_p", "version": 1}]
    tracer._decisions = [{"span_id": "", "name": "orphan_d", "reason": "无归属"}]

    await flush_to_langfuse(tracer, langfuse_config=_CFG)

    update_calls = mock_langfuse.update_current_span.call_args_list
    assert update_calls
    md = update_calls[0].kwargs.get("metadata") or {}
    assert "decisions" in md and len(md["decisions"]) == 1
    assert "prompt_versions" in md and len(md["prompt_versions"]) == 1


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