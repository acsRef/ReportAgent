"""Embedding 韧性测试：超时配置、错误分类重试、LRU 缓存、trace span。

全部离线——用假 client / 假 operation，不打真实 SiliconFlow API。
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

import app.embedding.service as service_module
from app.embedding.service import EmbeddingService

pytestmark = pytest.mark.smoke


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.siliconflow.cn/v1/embeddings")


def _status_error(cls, code: int):
    req = _req()
    resp = httpx.Response(code, request=req)
    return cls(message="boom", response=resp, body=None)


def _conn_error() -> APIConnectionError:
    return APIConnectionError(request=_req())


class _FakeEmbeddings:
    def __init__(self, owner: "_FakeClient"):
        self._owner = owner

    async def create(self, **kwargs):
        self._owner.create_awaits += 1
        if self._owner.raise_exc is not None:
            raise self._owner.raise_exc
        data = kwargs["input"]
        if isinstance(data, str):
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.5])])
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.5]) for _ in data]
        )


class _FakeClient:
    def __init__(self, raise_exc: Exception | None = None):
        self.create_awaits = 0
        self.raise_exc = raise_exc
        self.embeddings = _FakeEmbeddings(self)


# --- 错误分类 ---


def test_classify_retryable_network_errors():
    assert EmbeddingService._classify_retryable(_conn_error()) is True
    assert EmbeddingService._classify_retryable(APITimeoutError(request=_req())) is True
    assert EmbeddingService._classify_retryable(_status_error(InternalServerError, 500)) is True
    assert EmbeddingService._classify_retryable(_status_error(RateLimitError, 429)) is True


def test_classify_non_retryable_auth_and_word_errors():
    assert EmbeddingService._classify_retryable(_status_error(AuthenticationError, 401)) is False
    assert EmbeddingService._classify_retryable(_status_error(BadRequestError, 400)) is False


# --- 重试 ---


@patch("app.embedding.service.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_retryable_then_success(mock_sleep):
    svc = EmbeddingService()
    calls: list[int] = []

    async def op():
        calls.append(1)
        if len(calls) < 3:
            raise _conn_error()
        return "ok"

    assert await svc._call_with_retry(op) == "ok"
    assert len(calls) == 3
    assert mock_sleep.await_count == 2


@patch("app.embedding.service.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_exhaustion_raises_last(mock_sleep):
    svc = EmbeddingService()
    calls: list[int] = []

    async def op():
        calls.append(1)
        raise _conn_error()

    with pytest.raises(APIConnectionError):
        await svc._call_with_retry(op)
    assert len(calls) == service_module._RETRIES


async def test_non_retryable_raises_without_retry():
    svc = EmbeddingService()
    calls: list[int] = []

    async def op():
        calls.append(1)
        raise _status_error(AuthenticationError, 401)

    with pytest.raises(AuthenticationError):
        await svc._call_with_retry(op)
    assert len(calls) == 1


# --- 缓存 ---


async def test_cache_hit_skips_api():
    svc = EmbeddingService()
    fake = _FakeClient()
    svc._client = fake
    svc._cache["hello"] = [0.1]

    assert await svc.embed("hello") == [0.1]
    assert fake.create_awaits == 0


async def test_cache_miss_then_hit_calls_api_once():
    svc = EmbeddingService()
    fake = _FakeClient()
    svc._client = fake

    assert await svc.embed("abc") == [0.5]
    assert fake.create_awaits == 1
    assert await svc.embed("abc") == [0.5]
    assert fake.create_awaits == 1
    assert "abc" in svc._cache


@patch("app.embedding.service.asyncio.sleep", new_callable=AsyncMock)
async def test_failure_is_not_cached(mock_sleep):
    svc = EmbeddingService()
    fake = _FakeClient(raise_exc=_status_error(RateLimitError, 429))
    svc._client = fake

    assert await svc.embed_or_none("xyz") is None
    assert "xyz" not in svc._cache


async def test_batch_misses_and_hits():
    svc = EmbeddingService()
    fake = _FakeClient()
    svc._client = fake
    svc._cache["hit"] = [0.9]

    result = await svc.embed_batch(["hit", "miss"])
    assert result == [[0.9], [0.5]]
    # 只有 miss 打到 API，且命中项不再重复请求
    assert fake.create_awaits == 1


# --- trace span ---


async def test_embed_records_span_when_tracer_present():
    from app.infra.trace.sdk import Tracer, _current_tracer

    svc = EmbeddingService()
    svc._client = _FakeClient()
    tracer = Tracer("t1")
    token = _current_tracer.set(tracer)
    try:
        await svc.embed("with-tracer")
    finally:
        _current_tracer.reset(token)
    assert len(tracer._spans) == 1
    assert tracer._spans[0].span_name == "embedding"


async def test_embed_without_tracer_no_error():
    svc = EmbeddingService()
    svc._client = _FakeClient()
    assert await svc.embed("no-tracer") == [0.5]


# --- 配置 ---


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_TIMEOUT", "45.0")
    monkeypatch.setenv("EMBEDDING_RETRIES", "5")
    monkeypatch.setenv("EMBEDDING_CACHE_SIZE", "7")
    importlib.reload(service_module)
    assert service_module._TIMEOUT == 45.0
    assert service_module._RETRIES == 5
    assert service_module._CACHE_SIZE == 7