"""turn 直连调用的 token 记账（plan 2026-11-08-llm-budget-and-accounting D1）。

这条路此前完全不记账：`ReasoningReply` 拿到 usage 用完就丢，`observability.llm_call`
与 Langfuse 里只有 adapter 路径的记录。本组用例钉住三件事：

1. 三个出口（非流式 / 流式 / async 两态）都写入**一条** LLMCall；
2. 写入的字段与 adapter 一致（model / prompt_tokens / completion_tokens / latency_ms /
   redact 过的 input 与 output）；
3. 没有 tracer 或记账本身抛错时，主链照常返回 reply（观测是附赠品）。
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from app.infra.trace import sdk as trace_sdk
from app.llm import reasoning_client

pytestmark = pytest.mark.contracts

_PROMPT = "查一下 13800138000 这个客户的单"
_MASKED_PROMPT = "查一下 138******00 这个客户的单"

_PAYLOAD: dict[str, Any] = {
    "choices": [
        {
            "message": {"content": '{"goal":"detail_rows"}', "reasoning_content": "先看时间范围"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 1200,
        "completion_tokens": 640,
        "completion_tokens_details": {"reasoning_tokens": 508},
    },
}


@pytest.fixture()
def tracer() -> Iterator[trace_sdk.Tracer]:
    t = trace_sdk.Tracer("trace-accounting")
    token = trace_sdk._current_tracer.set(t)
    try:
        yield t
    finally:
        trace_sdk._current_tracer.reset(token)


# ── 假 transport ──────────────────────────────────────────────────────────


class _Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict[str, Any]:
        return self._payload


class _Stream:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def iter_lines(self) -> Iterator[str]:
        for line in self._lines:
            yield line


class _StreamCtx:
    def __init__(self, stream: _Stream) -> None:
        self._stream = stream

    def __enter__(self) -> _Stream:
        return self._stream

    def __exit__(self, *args: Any) -> None:
        return None


class _AsyncPlainResponse(_Response):
    """async 非流式出口用：`json()` 与同步版一样直接返回（代码里不 await）。"""


class _AsyncStreamResponse(_Stream):
    async def aiter_lines(self) -> Any:
        for line in self._lines:
            yield line


class _AsyncCtx:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def __aenter__(self) -> Any:
        return self._response

    async def __aexit__(self, *args: Any) -> None:
        return None


class _AsyncClient:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def __aenter__(self) -> "_AsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        return self._response

    def stream(self, *args: Any, **kwargs: Any) -> _AsyncCtx:
        return _AsyncCtx(self._response)


def _sse(**chunk: Any) -> str:
    return "data: " + json.dumps(chunk, ensure_ascii=False)


_STREAM_LINES = [
    _sse(choices=[{"delta": {"reasoning_content": "先看"}}]),
    _sse(choices=[{"delta": {"content": '{"goal":"detail_rows"}'}}]),
    _sse(choices=[{"delta": {}, "finish_reason": "stop"}]),
    _sse(usage=_PAYLOAD["usage"]),
    "data: [DONE]",
]


def _assert_recorded(tracer: trace_sdk.Tracer, *, model: str = "turn-model") -> None:
    assert len(tracer._llm_calls) == 1
    call = tracer._llm_calls[0]
    assert call.model == model
    assert call.prompt_tokens == 1200
    assert call.completion_tokens == 640
    assert call.latency_ms >= 0
    assert call.input == _MASKED_PROMPT  # PII 掩码在写入前完成
    assert call.output == '{"goal":"detail_rows"}'


# ── 1. 非流式 ─────────────────────────────────────────────────────────────


class TestSyncAccounting:
    def test_非流式写入一条记账(
        self, tracer: trace_sdk.Tracer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(reasoning_client.httpx, "post", lambda *a, **k: _Response(_PAYLOAD))
        reply = reasoning_client.chat_with_reasoning(_PROMPT, model="turn-model")

        assert reply is not None
        assert reply.prompt_tokens == 1200
        assert reply.completion_tokens == 640
        _assert_recorded(tracer)


# ── 2. 流式 ───────────────────────────────────────────────────────────────


class TestStreamAccounting:
    def test_流式写入一条记账_usage_来自尾帧(
        self, tracer: trace_sdk.Tracer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            reasoning_client.httpx,
            "stream",
            lambda *a, **k: _StreamCtx(_Stream(_STREAM_LINES)),
        )
        reply = reasoning_client.chat_with_reasoning(
            _PROMPT, model="turn-model", on_delta=lambda kind, text: None,
        )

        assert reply is not None
        assert reply.truncated is True  # 508 >= 512 - 4
        _assert_recorded(tracer)


# ── 3. async 两态 ─────────────────────────────────────────────────────────


class TestAsyncAccounting:
    async def test_async_非流式写入一条记账(
        self, tracer: trace_sdk.Tracer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            reasoning_client.httpx,
            "AsyncClient",
            lambda *a, **k: _AsyncClient(_AsyncPlainResponse(_PAYLOAD)),
        )
        reply = await reasoning_client.chat_with_reasoning_async(_PROMPT, model="turn-model")

        assert reply is not None
        _assert_recorded(tracer)

    async def test_async_流式写入一条记账(
        self, tracer: trace_sdk.Tracer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            reasoning_client.httpx,
            "AsyncClient",
            lambda *a, **k: _AsyncClient(_AsyncStreamResponse(_STREAM_LINES)),
        )
        reply = await reasoning_client.chat_with_reasoning_async(
            _PROMPT, model="turn-model", on_delta=lambda kind, text: None,
        )

        assert reply is not None
        _assert_recorded(tracer)


# ── 4. 观测失败不影响主链 ─────────────────────────────────────────────────


class TestAccountingNeverBreaksMainPath:
    def test_没有_tracer_时照常返回(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(reasoning_client.httpx, "post", lambda *a, **k: _Response(_PAYLOAD))
        reply = reasoning_client.chat_with_reasoning(_PROMPT, model="turn-model")

        assert reply is not None
        assert reply.content == '{"goal":"detail_rows"}'

    def test_记账抛错时照常返回(
        self, tracer: trace_sdk.Tracer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("observability down")

        monkeypatch.setattr(tracer, "add_llm_call", _boom)
        monkeypatch.setattr(reasoning_client.httpx, "post", lambda *a, **k: _Response(_PAYLOAD))
        reply = reasoning_client.chat_with_reasoning(_PROMPT, model="turn-model")

        assert reply is not None
        assert reply.reasoning == "先看时间范围"

    def test_usage_缺失时记_0_而不是崩(
        self, tracer: trace_sdk.Tracer, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        payload = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        monkeypatch.setattr(reasoning_client.httpx, "post", lambda *a, **k: _Response(payload))
        reply = reasoning_client.chat_with_reasoning("q", model="turn-model")

        assert reply is not None
        assert (reply.prompt_tokens, reply.completion_tokens) == (0, 0)
        assert len(tracer._llm_calls) == 1
        assert tracer._llm_calls[0].prompt_tokens == 0


class TestParseTokens:
    def test_脏值归零(self) -> None:
        assert reasoning_client._parse_tokens(None) == (0, 0)
        assert reasoning_client._parse_tokens({"prompt_tokens": "abc"}) == (0, 0)
        assert reasoning_client._parse_tokens({"prompt_tokens": 7, "completion_tokens": 3}) == (7, 3)
