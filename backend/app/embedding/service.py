from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import OrderedDict

from openai import AsyncOpenAI

from app.infra.trace.sdk import current_tracer

logger = logging.getLogger(__name__)

# 超时/重试/缓存上限均可由环境变量覆盖，默认值贴合 SiliconFlow OpenAI 兼容
# 接口的现实：明文 P95 到 20s，故超时给 30s 余量；网络抖动/限流值得退避重试，
# 认证类错误重试必复现、直接失败。
_TIMEOUT = float(os.getenv("EMBEDDING_TIMEOUT", "30.0"))
_RETRIES = int(os.getenv("EMBEDDING_RETRIES", "3"))
_CACHE_SIZE = int(os.getenv("EMBEDDING_CACHE_SIZE", "1024"))


class EmbeddingService:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        dimension: int = 1536,
    ):
        self._api_key = api_key or os.getenv("SILICONFLOW_API_KEY", "")
        self._base_url = base_url or os.getenv(
            "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
        )
        self._model = model or os.getenv("EMBEDDING_MODEL", "Qwen/Qwen2.5-7B-Instruct")
        self._dimension = dimension
        self._client: AsyncOpenAI | None = None
        # 进程内 LRU：文本→向量是确定性映射，冷启动后稳定复现，无需 TTL。
        # 只缓存成功结果；失败路径每次重试，不把失败固化进缓存。
        self._cache: "OrderedDict[str, list[float]]" = OrderedDict()
        self._cache_size = _CACHE_SIZE

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=_TIMEOUT,
                # 自定义 _call_with_retry 负责重试，客户端只保留首次尝试。
                max_retries=0,
            )
        return self._client

    async def embed(self, text: str) -> list[float]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached

        tracer = current_tracer()
        if tracer is not None:
            with tracer.span("embedding", span_type="TOOL", input=text):
                embedding = await self._embed_text(text)
        else:
            embedding = await self._embed_text(text)

        self._cache_set(text, embedding)
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float] | None] = [None] * len(texts)
        missing_idx: list[int] = []
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                out[i] = cached
            else:
                missing_idx.append(i)

        if missing_idx:
            tracer = current_tracer()
            if tracer is not None:
                with tracer.span("embedding_batch", span_type="TOOL", input=texts):
                    await self._fill_missing(texts, missing_idx, out)
            else:
                await self._fill_missing(texts, missing_idx, out)

        return [x for x in out if x is not None]

    async def embed_or_none(self, text: str) -> list[float] | None:
        try:
            return await self.embed(text)
        except Exception as exc:
            logger.warning("Embedding failed, falling back to keyword search: %s", exc)
            return None

    # --- 私有实现 ---

    async def _embed_text(self, text: str) -> list[float]:
        async def _call():
            resp = await self.client.embeddings.create(
                model=self._model,
                input=text,
                dimensions=self._dimension,
            )
            return resp.data[0].embedding
        return await self._call_with_retry(_call)

    async def _fill_missing(
        self,
        texts: list[str],
        missing_idx: list[int],
        out: list[list[float] | None],
    ) -> None:
        missing_texts = [texts[i] for i in missing_idx]
        resp = await self._call_with_retry(
            lambda: self.client.embeddings.create(
                model=self._model,
                input=missing_texts,
                dimensions=self._dimension,
            )
        )
        for i, data in zip(missing_idx, resp.data):
            embedding = data.embedding
            out[i] = embedding
            self._cache_set(texts[i], embedding)

    async def _call_with_retry(self, operation) -> object:
        """对瞬时故障做指数退避重试；认证/参数类错误立即抛出。

        可重试：连接错误、超时、5xx、429（RateLimit）。每次失败按
        2^attempt 秒退避 + 少量抖动，避免多请求同时重试打爆 API。
        """
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                return await operation()
            except Exception as exc:
                last_exc = exc
                if not self._classify_retryable(exc):
                    raise
                if attempt < _RETRIES - 1:
                    backoff = (2 ** attempt) + random.uniform(0, 0.5)
                    await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _classify_retryable(exc: Exception) -> bool:
        """瞬时故障才重试；认证/参数语义错误重试必复现，直接失败。"""
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
        return isinstance(
            exc, (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
        )

    def _cache_set(self, text: str, embedding: list[float]) -> None:
        self._cache[text] = embedding
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


_embedder: EmbeddingService | None = None


def get_embedder() -> EmbeddingService:
    global _embedder
    if _embedder is None:
        # B-6: 维度必须来自 EMBEDDING_DIM，与 main.py 启动校验和 init_pg.sql 的
        # VECTOR(n) 同源。此前硬编码默认 1536，运维改 EMBEDDING_DIM 匹配
        # vector(1024) 也不生效，启动校验永远报 actual_dim=1536 的死结。
        dimension = int(os.getenv("EMBEDDING_DIM", "1536"))
        _embedder = EmbeddingService(dimension=dimension)
    return _embedder