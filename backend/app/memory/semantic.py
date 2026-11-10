"""Semantic memory domain view（P4b §六 + cumulative review F3）。

thin view 委托 `MemoryManager.recall_structured`，过滤 UserMemory 召回条目
（source ∈ {memory_semantic, memory_preference}）。由 ContextRuntime 经本 view
调用——不直接 import infra.memory.memory_manager（宪法 §6 + plan §F3）。

2026-11-10（plan `semantic-memory-merge` D2）：**口径分歧提示落在这一层**——
「同一指标有多条不同口径」是**语义判断**，属于 domain；infra 只懂存取，
不能反向 import `app.memory.merge`（`test_memory_conversation_decouple` 钉着这条）。
"""
from __future__ import annotations

from app.infra.memory.memory_manager import MemoryManager
from app.memory.merge import atoms


def _multi_definition_hint(items: list[dict]) -> str | None:
    """同一指标命中多条**不同**内容 → 一句口径分歧提示（否则 None）。

    为什么不做自动合并：真 embedding 测量显示「不同口径」与「同义说法」的相似度**重叠**
    （`net_sales` vs `received` 只有 0.64，而无关句对能到 0.95），自动覆盖会静默改掉
    用户的口径。这里把分歧暴露给模型，由它在回答里说明，而不是替用户选。
    """
    buckets: dict[str, set[str]] = {}
    for item in items:
        for value in atoms(item.get("raw_text") or "").get("metric") or set():
            buckets.setdefault(value, set()).add((item.get("raw_text") or "")[:60])
    ambiguous = {k: v for k, v in buckets.items() if len(v) > 1}
    if not ambiguous:
        return None
    parts = [
        f"{metric}：{len(contents)} 条不同口径（" + "；".join(sorted(contents)) + "）"
        for metric, contents in sorted(ambiguous.items())
    ]
    return "[口径提示] 记忆里同一指标存在多条口径，请与用户确认：" + " / ".join(parts)


async def recall_structured(
    query: str,
    user_id: str,
    *,
    top_k_preferences: int = 3,
    session_id: str | None = None,
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
        session_id=session_id,
    )
    kept = [i for i in items if i["source"] in ("memory_semantic", "memory_preference")]
    hint = _multi_definition_hint(kept)
    if hint:
        kept.append({
            "raw_text": hint,
            "source": "memory_semantic", "kind": "semantic",
            "score": 0.0, "ref_id": None,
        })
    return kept
