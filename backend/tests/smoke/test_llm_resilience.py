"""LLM 韧性测试：令牌桶限流、错误分类重试、指数退避、90s 总超时。

全部离线——假 operation / 假 llm，不打真实 MiniMax API。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

import app.llm_resilience as resilience
from app.llm_resilience import (
    LLMRateLimitExceeded,
    LLMTimeoutError,
    _TokenBucket,
    invoke_with_retry,
)
import app.llm as llm_module

pytestmark = pytest.mark.smoke


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.minimax.chat/v1/chat/completions")


def _status_error(cls, code: int):
    req = _req()
    resp = httpx.Response(code, request=req)
    return cls(message="boom", response=resp, body=None)


def _conn_error() -> APIConnectionError:
    return APIConnectionError(request=_req())


# --- 令牌桶限流 ---


def test_token_bucket_acquires_within_capacity():
    bucket = _TokenBucket(rate=1000, capacity=10)
    for _ in range(10):
        assert bucket.acquire(timeout=0.1) is True
    # 容量耗尽，rate 极高但补满需时间——带 timeout 的 acquisition 应失败
    assert bucket.acquire(timeout=0.0) is False


def test_token_bucket_refills_over_time():
    bucket = _TokenBucket(rate=1000, capacity=1)
    assert bucket.acquire(timeout=0.1) is True
    assert bucket.acquire(timeout=0.0) is False
    # 等待 0.01s，rate=1000 → 应累计约 10 个 token，足够再取
    import time as _t
    _t.sleep(0.05)
    assert bucket.acquire(timeout=0.1) is True


# --- 错误分类 ---


def test_classify_retryable_errors():
    assert resilience._classify_retryable(_status_error(RateLimitError, 429)) is True
    assert resilience._classify_retryable(_status_error(InternalServerError, 500)) is True
    assert resilience._classify_retryable(_conn_error()) is True
    assert resilience._classify_retryable(APITimeoutError(request=_req())) is True


def test_classify_non_retryable_errors():
    assert resilience._classify_retryable(_status_error(AuthenticationError, 401)) is False
    assert resilience._classify_retryable(_status_error(BadRequestError, 400)) is False


# --- 重试 ---


@patch("app.llm_resilience.time.sleep")
def test_retry_then_success(mock_sleep):
    calls: list[int] = []

    def op():
        calls.append(1)
        if len(calls) < 3:
            raise _status_error(RateLimitError, 429)
        return "ok"

    assert invoke_with_retry(op, max_retries=5, max_total_time=60) == "ok"
    assert len(calls) == 3
    assert mock_sleep.call_count == 2
    # 退避递增：第一次 < 第二次
    sleeps = [c.args[0] for c in mock_sleep.call_args_list]
    assert sleeps[0] < sleeps[1]


def test_non_retryable_raises_immediately():
    calls: list[int] = []

    def op():
        calls.append(1)
        raise _status_error(AuthenticationError, 401)

    with pytest.raises(AuthenticationError):
        invoke_with_retry(op, max_retries=5, max_total_time=60)
    assert len(calls) == 1


@patch("app.llm_resilience.time.sleep")
def test_retries_exhausted_raises_last_original(mock_sleep):
    def op():
        raise _status_error(RateLimitError, 429)

    with pytest.raises(RateLimitError):
        invoke_with_retry(op, max_retries=2, max_total_time=60)
    assert mock_sleep.call_count == 2


def test_total_budget_exhausted_raises_timeout():
    class _AlwaysBucket:
        def acquire(self, timeout):
            return True

    # deadline = 100 + 90 = 190；第二次读 monotonic 返回 200 → remaining = -10 → 超预算
    with patch.object(resilience, "_rate_limiter", _AlwaysBucket()), \
         patch("app.llm_resilience.time.monotonic", side_effect=[100.0, 200.0]):
        def op():
            raise _status_error(RateLimitError, 429)

        with pytest.raises(LLMTimeoutError):
            invoke_with_retry(op, max_retries=5, max_total_time=90)


def test_rate_limit_blocked_raises_rate_limit_exceeded():
    class _EmptyBucket:
        def acquire(self, timeout):
            return False

    with patch.object(resilience, "_rate_limiter", _EmptyBucket()):
        with pytest.raises(LLMRateLimitExceeded):
            invoke_with_retry(lambda: "x", max_total_time=60)


# --- call_llm 接线 ---


def test_call_llm_uses_retry_and_returns_text():
    calls: list[int] = []

    class _FakeLLM:
        def invoke(self, prompt):
            calls.append(1)
            if len(calls) < 3:
                raise _status_error(RateLimitError, 429)
            return SimpleNamespace(content="  final answer  ", usage_metadata={})

    fake_llm = _FakeLLM()
    with patch("app.llm.adapter.ChatOpenAI", lambda **kw: fake_llm), \
         patch("app.infra.trace.sdk.current_tracer", lambda: None), \
         patch("app.llm_resilience.time.sleep"):
        result = llm_module.call_llm("some prompt")
    assert result == "final answer"
    assert len(calls) == 3