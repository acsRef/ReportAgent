"""思考链路流式可见（plan 2026-11-05-streaming-reasoning）。

覆盖：
- `reasoning_client` 流式解析（reasoning_content + content 累计、usage 判定截断、断流不炸）；
- 预算与总额的成对关系（只调预算不会把回答挤掉）；
- `reasoning_stream` 节流发布 / 关闭开关 / 未安装时静默。
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from app.infra.execution import reasoning_stream
from app.llm import reasoning_client

pytestmark = pytest.mark.contracts


# ── 预算 / 总额 ───────────────────────────────────────────────────────────


class TestTokenBudgets:
    def test_默认总额随预算抬高(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TURN_MAX_TOKENS", raising=False)
        assert reasoning_client.resolve_max_tokens(512) == 1412
        assert reasoning_client.resolve_max_tokens(1024) == 1924

    def test_显式总额优先(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURN_MAX_TOKENS", "4000")
        assert reasoning_client.resolve_max_tokens(512) == 4000

    def test_预算可被_env_覆盖(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURN_THINKING_BUDGET", "1024")
        assert reasoning_client.resolve_thinking_budget() == 1024
        assert reasoning_client.resolve_thinking_budget(2048) == 2048

    def test_usage_判定截断(self) -> None:
        usage = {"completion_tokens_details": {"reasoning_tokens": 508}}
        assert reasoning_client._parse_usage(usage, "", 512) == (508, True)
        assert reasoning_client._parse_usage(usage, "", 2048) == (508, False)
        assert reasoning_client._parse_usage({}, "length", 2048) == (0, True)


# ── 流式解析 ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def iter_lines(self) -> Iterator[str]:
        for line in self._lines:
            yield line


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> _FakeResponse:
        return self._response

    def __exit__(self, *args: Any) -> None:
        return None


def _sse(**chunk: Any) -> str:
    return "data: " + json.dumps(chunk, ensure_ascii=False)


class TestStreamingParsing:
    def test_累计_reasoning_并回调(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = [
            _sse(choices=[{"delta": {"reasoning_content": "先看"}}]),
            _sse(choices=[{"delta": {"reasoning_content": "时间范围"}}]),
            _sse(choices=[{"delta": {"content": "{\"a\":1}"}}]),
            _sse(usage={"completion_tokens_details": {"reasoning_tokens": 120}}),
            "data: [DONE]",
        ]
        monkeypatch.setattr(
            reasoning_client.httpx, "stream", lambda *a, **k: _FakeStream(_FakeResponse(lines)),
        )
        seen: list[tuple[str, str]] = []
        reply = reasoning_client.chat_with_reasoning(
            "q", model="m", on_delta=lambda kind, text: seen.append((kind, text)),
        )

        assert reply is not None
        assert reply.reasoning == "先看时间范围"
        assert reply.content == '{"a":1}'
        # 回调拿到的是**累计**文本（前端因此能直接替换，不用自己拼）
        assert seen[0] == ("reasoning", "先看")
        assert seen[1] == ("reasoning", "先看时间范围")
        assert seen[-1] == ("content", '{"a":1}')
        assert reply.truncated is False  # 120 < 512

    def test_断流但有内容时尽力返回(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BreakingResponse(_FakeResponse):
            def iter_lines(self) -> Iterator[str]:
                yield _sse(choices=[{"delta": {"reasoning_content": "想了"}}])
                raise RuntimeError("stream broke")

        monkeypatch.setattr(
            reasoning_client.httpx, "stream",
            lambda *a, **k: _FakeStream(_BreakingResponse([])),
        )
        reply = reasoning_client.chat_with_reasoning("q", model="m", on_delta=lambda *a: None)
        assert reply is not None and reply.reasoning == "想了"

    def test_一个字符都没收到才算失败(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Boom(_FakeResponse):
            def iter_lines(self) -> Iterator[str]:
                raise RuntimeError("boom")
                yield ""  # pragma: no cover

        monkeypatch.setattr(
            reasoning_client.httpx, "stream", lambda *a, **k: _FakeStream(_Boom([])),
        )
        assert reasoning_client.chat_with_reasoning("q", model="m", on_delta=lambda *a: None) is None


# ── 节流 sink ─────────────────────────────────────────────────────────────


class TestReasoningStreamSink:
    def test_未安装时静默(self) -> None:
        reasoning_stream.emit("随便写点")  # 不应抛错

    def test_按间隔节流且帧形态正确(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURN_STREAM_INTERVAL_MS", "350")
        frames: list[dict] = []
        token = reasoning_stream.install(frames.append)
        try:
            reasoning_stream.emit("第一段")        # 首帧必发
            reasoning_stream.emit("第一段第二段")  # 间隔内 → 丢
            assert len(frames) == 1
            payload = json.loads(frames[0]["data"])
            assert frames[0]["event"] == "trace"
            assert payload["step"] == reasoning_stream.DEFAULT_STEP
            assert payload["status"] == "running"
            assert payload["reasoning"] == "第一段"

            # 把上次发送时间往前拨，模拟已过间隔
            sink = reasoning_stream._sink.get()
            assert sink is not None
            sink._last_emit -= 1.0
            reasoning_stream.emit("第一段第二段第三段")
            assert len(frames) == 2
            assert json.loads(frames[1]["data"])["reasoning"] == "第一段第二段第三段"
        finally:
            reasoning_stream.reset(token)

    def test_关闭开关后不发布(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURN_REASONING_STREAM", "0")
        frames: list[dict] = []
        token = reasoning_stream.install(frames.append)
        try:
            reasoning_stream.emit("第一段")
        finally:
            reasoning_stream.reset(token)
        assert frames == []

    def test_发布失败不影响调用方(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(frame: dict) -> None:
            raise RuntimeError("registry down")

        monkeypatch.setenv("TURN_STREAM_INTERVAL_MS", "50")
        token = reasoning_stream.install(boom)
        try:
            reasoning_stream.emit("第一段")  # 不抛
        finally:
            reasoning_stream.reset(token)

    def test_帧内文本按上限保留尾部(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frames: list[dict] = []
        token = reasoning_stream.install(frames.append)
        try:
            reasoning_stream.emit("x" * (reasoning_stream.MAX_FRAME_CHARS + 100))
        finally:
            reasoning_stream.reset(token)
        payload = json.loads(frames[0]["data"])
        assert len(payload["reasoning"]) == reasoning_stream.MAX_FRAME_CHARS
