"""单轮 request budget（plan 2026-11-08-llm-budget-and-accounting D3）。

改前最坏耗时 = 思考首轮 90s 超时 + 不思考重试 45s = 135s，且第二次也超时的话
用户白等一场照样回落。现在整轮共用一个 deadline（`TURN_REQUEST_BUDGET`，默认 120s）：

- 首轮 `timeout = min(单次超时, 剩余)`；
- 剩余 < 20s → **不重试**（`meta["retry_skipped"] == "budget"`），直接回落；
- 否则重试 `timeout = min(快速超时, 剩余-5)`。

本文件只钉这条预算行为，模型/解析行为在 `test_live_session_issue_fixes.py`。
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from app.agent import turn_understanding
from app.llm import budgets
from app.llm.reasoning_client import ReasoningReply

pytestmark = pytest.mark.contracts

_OK = ReasoningReply(
    content='{"conversation_act": "new_task"}', reasoning="想完了", model="m",
)


def _fake_calls(
    monkeypatch: pytest.MonkeyPatch, attr: str = "chat_with_reasoning", *, async_: bool = False,
) -> list[dict]:
    """让每次调用都失败（返回 None），返回 kwargs 列表。"""
    calls: list[dict] = []

    if async_:
        async def afake(prompt: str, **kwargs: Any) -> None:
            calls.append(kwargs)
            return None

        monkeypatch.setattr(f"app.llm.reasoning_client.{attr}", afake)
        return calls

    def fake(prompt: str, **kwargs: Any) -> None:
        calls.append(kwargs)
        return None

    monkeypatch.setattr(f"app.llm.reasoning_client.{attr}", fake)
    return calls


class TestFirstTimeout:
    def test_首轮超时受剩余时间约束(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch)
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "40")

        turn_understanding.understand_turn("查销售额")

        assert calls[0]["timeout"] <= 40.0

    def test_预算宽松时首轮用单次超时(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch)
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "300")
        monkeypatch.setenv("LLM_TURN_TIMEOUT", "90")

        turn_understanding.understand_turn("查销售额")

        assert calls[0]["timeout"] == 90.0

    def test_deadline_已过时至少留一秒(self) -> None:
        assert turn_understanding._first_timeout(time.monotonic() - 100) == 1.0


class TestRetryDecision:
    def test_预算充足时重试一次(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch)
        meta: dict = {}
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "120")

        turn_understanding.understand_turn("查销售额", meta=meta)

        assert len(calls) == 2
        assert calls[1]["enable_thinking"] is False
        assert calls[1]["timeout"] == budgets.turn_fast_timeout()
        assert "retry_skipped" not in meta

    def test_剩余不足二十秒不再重试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch)
        meta: dict = {}
        # 首轮吃光 23s 左右，剩余 < 20s → 不该再赌一次
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "23")

        plan, reasoning = turn_understanding.understand_turn("查销售额", meta=meta)

        assert len(calls) == 1
        assert plan is None and reasoning == ""
        assert meta["retry_skipped"] == "budget"
        assert meta["failed"] is True

    def test_重试超时被剩余时间夹住(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch)
        # 首轮 40s + 重试最多 30s < 120s，重试拿满快速超时；
        # 但若快速超时被配到很大，就必须被「剩余-5」夹住
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "40")
        monkeypatch.setenv("LLM_TURN_FAST_TIMEOUT", "600")

        turn_understanding.understand_turn("查销售额")

        assert len(calls) == 2
        assert calls[1]["timeout"] <= 40.0
        assert calls[1]["timeout"] < budgets.turn_fast_timeout()

    def test_显式关闭思考时不重试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch)

        turn_understanding.understand_turn("查销售额", enable_thinking=False)

        assert len(calls) == 1
        assert calls[0]["enable_thinking"] is False


class TestAsyncSameSemantics:
    async def test_预算用尽时不重试(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _fake_calls(monkeypatch, "chat_with_reasoning_async", async_=True)
        meta: dict = {}
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "23")

        plan, _reasoning = await turn_understanding.understand_turn_async("查销售额", meta=meta)

        assert len(calls) == 1
        assert plan is None
        assert meta["retry_skipped"] == "budget"

    async def test_预算充足时重试并成功(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict] = []

        async def fake(prompt: str, **kwargs: Any) -> ReasoningReply | None:
            calls.append(kwargs)
            return None if len(calls) == 1 else _OK

        monkeypatch.setattr("app.llm.reasoning_client.chat_with_reasoning_async", fake)
        meta: dict = {}
        monkeypatch.setenv("TURN_REQUEST_BUDGET", "120")

        plan, _reasoning = await turn_understanding.understand_turn_async("查销售额", meta=meta)

        assert plan is not None
        assert len(calls) == 2
        assert meta["retried_without_thinking"] is True


class TestRetryTimeoutHelper:
    def test_剩余不足返回_None(self) -> None:
        assert turn_understanding._retry_timeout(time.monotonic() + 10) is None

    def test_剩余充足返回快速超时(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_TURN_FAST_TIMEOUT", raising=False)
        assert turn_understanding._retry_timeout(time.monotonic() + 120) == 30.0
