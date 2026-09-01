"""Langfuse SDK 单例（fail-open）。"""
from __future__ import annotations

import logging
from typing import Any

from app.observability.langfuse_config import LangfuseConfig

logger = logging.getLogger(__name__)

_client: Any | None = None


def get_langfuse_client() -> Any | None:
    """返回 Langfuse SDK client 单例；enabled=False 或 init 失败 → None。"""
    global _client
    cfg = LangfuseConfig()
    if not cfg.enabled:
        return None
    if _client is None:
        try:
            from langfuse import Langfuse  # 延迟 import，未装 langfuse 时优雅降级
            _client = Langfuse(
                public_key=cfg.public_key,
                secret_key=cfg.secret_key,
                host=cfg.host,
            )
        except Exception as exc:
            logger.warning("langfuse init failed: %s", exc)
            _client = None
    return _client