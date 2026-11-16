"""执行中上下文溢出恢复（DSH 第三触发 `context-overflow`）。

两层：

1. **分类**：`is_context_overflow_error` 只认 provider 明确说窗口放不下；
   认证/参数错不能被误判成溢出，否则会多烧一次「压缩 + 重试」。
2. **接线**：`_invoke_sql_agent_with_overflow_recovery` 在 SQL 子图抛溢出时，
   强制全量折叠（`retain=0`）→ 重建上下文 → **只重试一次**；第二次还炸就把异常抛出去
   （DSH `maxOverflowRetries = 1`，不做压缩风暴）。

这里不打桩 SQL 子图内部：`invoke` / `build_state` 都是注入的，测的是**接线语义**本身。
"""
from __future__ import annotations

import asyncio

import pytest

from app.agent import confirmed_execution_graph as ceg
from app.reliability.errors import (
    AGENT_RECOVERABLE_KINDS,
    USER_RECOVERABLE_KINDS,
    is_context_overflow_error,
    user_message,
)

pytestmark = pytest.mark.contracts


class _ProviderError(Exception):
    """模拟 openai BadRequestError 的形状：类名 + 文案都由 provider 给。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 400


class TestClassification:
    @pytest.mark.parametrize("message", [
        "Error code: 400 - {'code': 'context_length_exceeded', 'message': '...'}",
        "This model's maximum context length is 65536 tokens",
        "Requested too many tokens, please reduce the length of the messages",
        "the context window is full",
        # 真机文案（SiliconFlow 20015）：此前五个标记全部不命中
        "number of input tokens (210003) has exceeded max_seq_len (163840) limit.",
    ])
    def test_认得出溢出(self, message: str) -> None:
        assert is_context_overflow_error(_ProviderError(message)) is True

    @pytest.mark.parametrize("message", [
        "Incorrect API key provided",
        "400 invalid_request_error: response_format is not supported",
        "Connection error.",
        "Rate limit reached for requests",
    ])
    def test_不会把别的错当溢出(self, message: str) -> None:
        assert is_context_overflow_error(_ProviderError(message)) is False

    def test_用户可恢复但不进agent修复表(self) -> None:
        # 用户能自救（缩范围/新环比点重试），但 agent repair 同一条 SQL 没意义
        assert "context_overflow" in USER_RECOVERABLE_KINDS
        assert "context_overflow" not in AGENT_RECOVERABLE_KINDS
        assert "缩小时间范围" in user_message("context_overflow")


class TestRecoveryWiring:
    @staticmethod
    def _run(**kwargs):
        return asyncio.run(ceg._invoke_sql_agent_with_overflow_recovery(**kwargs))

    def test_溢出时压缩一次并用新上下文重试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        forced: list[dict] = []

        async def fake_forced(**kwargs) -> tuple[str, str]:
            forced.append(kwargs)
            return "COMPACTED-CONV", "COMPACTED-ASM"

        async def fake_invoke(payload: dict) -> dict:
            calls.append(payload["conversation_context"])
            if len(calls) == 1:
                raise _ProviderError("maximum context length is 65536 tokens")
            return {"ok": True}

        monkeypatch.setattr(ceg, "_forced_compaction_context", fake_forced)

        out = self._run(
            session_id="s1", user_id=1, user_query="看一下华东", state_dict={"session_id": "s1"},
            conversation_context="OLD-CONV", assembled_context="OLD-ASM",
            build_state=lambda conv, asm: {"conversation_context": conv, "assembled_context": asm},
            invoke=fake_invoke,
        )

        assert out == {"ok": True}
        assert calls == ["OLD-CONV", "COMPACTED-CONV"], "必须用**新**上下文重跑一次"
        assert len(forced) == 1 and forced[0]["session_id"] == "s1"

    def test_非溢出错误直接抛不压缩(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forced: list[dict] = []

        async def fake_forced(**kwargs) -> tuple[str, str]:  # pragma: no cover - 不该被调用
            forced.append(kwargs)
            return "", ""

        async def fake_invoke(payload: dict) -> dict:
            raise _ProviderError("Incorrect API key provided")

        monkeypatch.setattr(ceg, "_forced_compaction_context", fake_forced)

        with pytest.raises(_ProviderError):
            self._run(
                session_id="s1", user_id=1, user_query="q", state_dict={},
                conversation_context="c", assembled_context="a",
                build_state=lambda conv, asm: {},
                invoke=fake_invoke,
            )
        assert forced == [], "非溢出错误不许触发压缩"

    def test_第二次还炸就抛出不再压缩(self, monkeypatch: pytest.MonkeyPatch) -> None:
        forced: list[dict] = []
        attempts = {"n": 0}

        async def fake_forced(**kwargs) -> tuple[str, str]:
            forced.append(kwargs)
            return "C2", "A2"

        async def always_overflow(payload: dict) -> dict:
            attempts["n"] += 1
            raise _ProviderError("maximum context length is 65536 tokens")

        monkeypatch.setattr(ceg, "_forced_compaction_context", fake_forced)

        with pytest.raises(_ProviderError):
            self._run(
                session_id="s1", user_id=1, user_query="q", state_dict={},
                conversation_context="c", assembled_context="a",
                build_state=lambda conv, asm: {},
                invoke=always_overflow,
            )
        assert attempts["n"] == 2, "只重试一次"
        assert len(forced) == 1, "不许压缩第二次（压缩风暴）"

    def test_压缩本身失败时用空上下文重试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_forced_compaction_context` 内部失败 → 返回空串，重试仍然发生。"""
        calls: list[str] = []

        async def fake_invoke(payload: dict) -> dict:
            calls.append(payload["conversation_context"])
            if len(calls) == 1:
                raise _ProviderError("context_length_exceeded")
            return {"ok": True}

        class _Boom:
            async def build(self, **kwargs):
                raise RuntimeError("db down")

        monkeypatch.setattr("app.context.runtime.ContextRuntime", lambda: _Boom())

        out = ceg._invoke_sql_agent_with_overflow_recovery  # noqa: F841 - 明确被测函数

        result = asyncio.run(
            ceg._invoke_sql_agent_with_overflow_recovery(
                session_id="s1", user_id=1, user_query="q", state_dict={},
                conversation_context="OLD", assembled_context="OLD-ASM",
                build_state=lambda conv, asm: {"conversation_context": conv},
                invoke=fake_invoke,
            )
        )
        assert result == {"ok": True}
        assert calls == ["OLD", ""], "压缩失败也要继续重试（空上下文总比不试好）"
