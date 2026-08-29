"""Query memory domain view（P4b §六 + cumulative review F3）。

thin view 委托 `MemoryManager.recall_structured`，过滤 QueryMemory 召回条目
（source = memory_query）。由 ContextRuntime 经本 view 调用——不直接 import
infra.memory.memory_manager（宪法 §6 + plan §F3）。
"""
from __future__ import annotations

from app.infra.memory.memory_manager import MemoryManager


async def recall_structured(
    query: str,
    user_id: str,
    *,
    top_k_queries: int = 2,
) -> list[dict]:
    """Recall query experience entries（source = memory_query）。

    返回 list[dict]，结构同构于 `app.context.assembler.RecallItem`
    （raw_text/source/kind/score/ref_id）。
    """
    items = await MemoryManager().recall_structured(
        query, user_id,
        top_k_queries=top_k_queries,
        top_k_preferences=0,
    )
    return [i for i in items if i["source"] == "memory_query"]