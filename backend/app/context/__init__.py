"""app.context package facade（P3 Task 4）。

外部 import 路径保持兼容：
    from app.context import build_context, build_session_context,
                            format_messages, format_context_block

新 API：
    from app.context.runtime import ContextRuntime
（也可 `from app.context import ContextRuntime`）

review P0 #1 决议：**facade `build_session_context` 直调 `_engine._prepare_conversation_context`，
不转发到 `ContextRuntime.build()`** —— 后者需 query/agent 入参且会引入
MemoryManager.recall 副作用；本 facade 与现 build_session_context 行为 100%
等价（无 recall / 无 decide 副作用）。
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "app.context 包：旧 build_session_context / build_context 等仍可用；"
    "新代码优先 from app.context.runtime import ContextRuntime。",
    DeprecationWarning,
    stacklevel=2,
)

# 旧 sync API（兼容路径）——实现在 app.memory.conversation（P4a 迁出）；此处 re-export
from app.context._engine import (  # noqa: E402
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
)
from app.memory.conversation import (  # noqa: E402  async glue（memory 域）
    prepare_conversation_context as _prepare_conversation_context,
)

# 新 API（re-export，让外部 `from app.context import ContextRuntime` 可用）
from app.context.assembler import (  # noqa: E402
    ContextAssembler,
    ContextBundle,
    RecallItem,
)
from app.context.decision import (  # noqa: E402
    ContextDecisionPolicy,
    LegacyFallbackPolicy,
    RecallDecision,
)
from app.context.policy import (  # noqa: E402
    AgentContextPolicy,
    ContextPolicyResolver,
)
from app.context.runtime import ContextRuntime, context_runtime  # noqa: E402


async def build_session_context(session_id: str, user_id: int | str) -> str:
    """P3 兼容 facade：直调 memory 域 conversation glue（review P0 #1）。

    **不**转发 ContextRuntime.build —— 后者需 query/agent 入参且引入
    MemoryManager.recall 副作用。本函数与 P2 行为逐字等价。
    """
    return await _prepare_conversation_context(session_id, user_id)


__all__ = [
    # 常量
    "RECENT_WINDOW", "COMPRESS_BATCH", "L2_MAX_CHARS", "L2_5_MAX_CHARS",
    "L2_ARCHIVE_INTERVAL",
    # 旧 sync API
    "build_context", "compress_and_extract", "archive_to_l2_5",
    "format_messages", "format_context_block",
    # 旧 async API（facade）
    "build_session_context",
    # 新 API
    "ContextRuntime", "context_runtime",
    "ContextDecisionPolicy", "RecallDecision", "LegacyFallbackPolicy",
    "AgentContextPolicy", "ContextPolicyResolver",
    "ContextBundle", "RecallItem", "ContextAssembler",
]
