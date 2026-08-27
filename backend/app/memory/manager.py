"""Memory 领域 manager（P4a Task 6）：L3 write seam。

伞形 plan §二·二 memory/ = domain/application 层。本模块把「Conversation Memory
压缩抽取出的事实写进长期记忆」这件事从 `context` 包里出来，收敛到 memory 域，
对外只暴露 `remember_conversation_facts`；底层单条写入**委托** `infra.memory`
的 `MemoryManager`（宪法 §6「读写一律经 Memory Manager」），mem0 增强经
`infra.memory.mem0_extractor`（domain→infra 合法方向）。

P4a 是 write pipeline 的占位 seam：现在只写 insight；P4b 在**同一函数**内接入
confidence 规则 + lifecycle（scope/status/session_id/expires_at），不再动 conversation.py。
"""
from __future__ import annotations

import logging

from app.infra.memory import mem0_extractor
from app.infra.memory.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def remember_conversation_facts(
    user_id: int | str, updates: dict, compressed_batch: list[dict],
) -> None:
    """把压缩抽取的结构化事实写进 L3（memory.semantic_entry）。mem0 增强可选。

    自 P3 `context/_engine._save_l3_facts` 迁入，唯一行为差异：单条写入不再
    直 `UserMemory().save(...)`，改走 `MemoryManager().remember_preference(...)`
    （memory_type=insight / source=context_compress / importance=0.5 与旧一致，
    保 P3 `test_build_session_context` L3 断言不破）。
    去重仍 `dict.fromkeys` 保序；任何单条失败降级不拖垮主链路。
    """
    facts: list[str] = []
    for s in updates.get("extracted_schemas") or []:
        if isinstance(s, dict) and s:
            facts.append(str(s))
    facts += [str(p) for p in updates.get("extracted_preferences") or [] if p]

    if compressed_batch:
        from app.memory.conversation import format_messages
        try:
            facts += await mem0_extractor.extract_facts(
                format_messages(compressed_batch), user_id,
            )
        except Exception as exc:
            logger.warning("mem0 augmentation failed: %s", exc)

    if not facts:
        return
    mm = MemoryManager()
    for fact in dict.fromkeys(facts):  # 保序去重
        try:
            await mm.remember_preference(
                user_id=str(user_id), content=fact, memory_type="insight",
                importance=0.5, source="context_compress",
            )
        except Exception as exc:
            logger.warning("save L3 fact failed: %s", exc)
