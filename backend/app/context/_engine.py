"""Compatibility re-export facade（P4a Task 6）。

P3：本文件承载 Conversation Engine 实现。
P4a：实现迁入 `app/memory/conversation.py`（domain 层）；本文件退化为薄 re-export，
      保两条 P3 兼容钉子不破：
      - `from app.context import build_context / format_context_block / ...`（外部 import）
      - `app.context.__init__.build_session_context` 的 async glue 入口

⚠️ monkeypatch 陷阱（P4a plan §2.4 一等公民）：re-export 后 `compress_and_extract`
的实际 globals 在 `app.memory.conversation`。`monkeypatch.setattr(_engine, "compress_and_extract", X)`
**不再**影响 `build_context` 内对它的调用。测试须改打 `app.memory.conversation`。
"""
from __future__ import annotations

from app.memory.conversation import (  # noqa: F401  re-export
    COMPRESS_BATCH,
    L2_5_MAX_CHARS,
    L2_ARCHIVE_INTERVAL,
    L2_MAX_CHARS,
    RECENT_WINDOW,
    archive_to_l2_5,
    build_context,
    compress_and_extract,
    format_context_block,
    format_messages,
    prepare_conversation_context,
)

# P3 私有别名：context 包内部与 test 曾以 `_prepare_conversation_context` 引用
_prepare_conversation_context = prepare_conversation_context

__all__ = [
    "RECENT_WINDOW", "COMPRESS_BATCH", "L2_MAX_CHARS", "L2_5_MAX_CHARS",
    "L2_ARCHIVE_INTERVAL", "format_messages", "format_context_block",
    "archive_to_l2_5", "compress_and_extract", "build_context",
    "prepare_conversation_context", "_prepare_conversation_context",
]
