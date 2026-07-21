from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=15.0,
                max_retries=1,
            )
        return self._client

    async def embed(self, text: str) -> list[float]:
        resp = await self.client.embeddings.create(
            model=self._model,
            input=text,
            dimensions=self._dimension,
        )
        return resp.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self.client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dimension,
        )
        return [d.embedding for d in resp.data]

    async def embed_or_none(self, text: str) -> list[float] | None:
        try:
            return await self.embed(text)
        except Exception as exc:
            logger.warning("Embedding failed, falling back to keyword search: %s", exc)
            return None


_embedder: EmbeddingService | None = None


def get_embedder() -> EmbeddingService:
    global _embedder
    if _embedder is None:
        _embedder = EmbeddingService()
    return _embedder
