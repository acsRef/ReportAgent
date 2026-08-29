"""Semantic memory domain view（P4b §六 + cumulative review F3）。

thin view 委托 `MemoryManager.recall_structured`，过滤 UserMemory 召回条目
（source ∈ {memory_semantic, memory_preference}）。由 ContextRuntime 经本 view
调用——不直接 import infra.memory.memory_manager（宪法 §6 + plan §F3）。
"""
from __future__ import annotations

from app.infra.memory.memory_manager import MemoryManager


async def recall_structured(
    query: str,
    user_id: str,
    *,
    top_k_preferences: int = 3,
) -> list[dict]:
    """Recall semantic memory entries（source = memory_semantic / memory_preference）。

    返回 list[dict]，结构同构于 `app.context.assembler.RecallItem`
    （raw_text/source/kind/score/ref_id）。**不** import context 类型：
    persistence/domain 层不反向依赖；调用方按 RecallItem 消费（TypedDict 运行时即 dict）。
    """
    items = await MemoryManager().recall_structured(
        query, user_id,
        top_k_queries=0,
        top_k_preferences=top_k_preferences,
    )
    return [i for i in items if i["source"] in ("memory_semantic", "memory_preference")]