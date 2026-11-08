"""Langfuse SDK 单例（fail-open）。"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.observability.langfuse_config import LangfuseConfig

logger = logging.getLogger(__name__)

_client: Any | None = None

# 2026-11-08（真机实测）：Langfuse v4 走 OTLP/HTTP，默认 read timeout 只有 5 秒。
# 一次完整一轮 trace 有几十个 observation，网络稍慢就 `Read timed out`，
# 而且 OTel exporter 是**整批丢弃**（日志里只有一行 ERROR，UI 上什么都看不到）。
_OTLP_TIMEOUT_MS = "15000"


def get_langfuse_client() -> Any | None:
    """返回 Langfuse SDK client 单例；enabled=False 或 init 失败 → None。"""
    global _client
    cfg = LangfuseConfig()
    if not cfg.enabled:
        return None
    if _client is None:
        try:
            # 必须在构造 client 之前设：OTel exporter 在 init 时读环境变量建连接池。
            os.environ.setdefault("OTEL_EXPORTER_OTLP_TIMEOUT", _OTLP_TIMEOUT_MS)
            # 批次切小：一轮 trace 几十个 observation 时，512 的大包更容易撞满 5s 超时。
            os.environ.setdefault("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "50")
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