"""Memory 领域 manager（P4b）：write pipeline + L3 write seam。

伞形 plan §二·二 memory/ = domain/application 层。承 P4a 的 `remember_conversation_facts`
seam，P4b 在此落地 §五 冻结的 **固定 confidence 规则**（不让 LLM 拍）+ §六 lifecycle：

- `remember_explicit_preference`：explicit user statement（MemoryPolicy 正则检测）
  → active stable_preference / confidence=high（§五 line 49）；写前 supersede 旧 active 同 content。
- `remember_inferred_facts`：LLM-inferred（compress_and_extract / mem0）→ status=candidate /
  confidence=low（§五：LLM inferred 不进 active）→ 被 recall 排除（infra active 过滤）。
- `remember_conversation_facts`：兼容入口，把 conversation 抽的事实路由到 candidate。

底层单条写入委托 `infra.memory.MemoryManager`（宪法 §6 网关）；mem0 经 `infra.memory`
（domain→infra 合法）。
"""
from __future__ import annotations

import logging

from app.infra.memory import mem0_extractor
from app.infra.memory.memory_manager import MemoryManager
from app.infra.memory.policy import MemoryPolicy
from app.memory.lifecycle import (
    CONFIDENCE_EXPLICIT_STATEMENT,
    CONFIDENCE_LLM_INFERRED,
    MemoryScope,
    MemoryStatus,
)

logger = logging.getLogger(__name__)

_policy = MemoryPolicy()


async def remember_conversation_facts(
    user_id: int | str, updates: dict, compressed_batch: list[dict],
) -> None:
    """兼容入口（conversation.prepare_conversation_context 仍调它，时机不变）。

    P4b：把 LLM 压缩抽取的 schema/preference facts 路由到 `remember_inferred_facts`
    → status=candidate（§五 line 49「LLM inferred 不进 active」）。这是相对
    P3/P4a 的**行为修正**：以前当 insight/active 写并被召回，现落 candidate 不被召回。
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
    await remember_inferred_facts(user_id, list(dict.fromkeys(facts)))  # 保序去重


async def remember_inferred_facts(
    user_id: int | str, facts: list[str], *, session_id: str | None = None,
) -> None:
    """LLM-inferred 事实 → status=candidate / confidence=low（§五）。单条失败降级。"""
    if not facts:
        return
    mm = MemoryManager()
    for fact in facts:
        try:
            await mm.remember_preference(
                user_id=str(user_id), content=fact, memory_type="insight",
                importance=0.5, source="context_compress",
                scope=MemoryScope.SESSION.value if session_id else MemoryScope.USER.value,
                status=MemoryStatus.CANDIDATE.value,
                confidence=CONFIDENCE_LLM_INFERRED.value,
                session_id=session_id,
            )
        except Exception as exc:
            logger.warning("save inferred fact failed: %s", exc)


async def remember_explicit_preference(
    user_id: int | str, text: str, *, source: str = "user_turn",
) -> int | None:
    """Explicit user statement → active stable_preference / confidence=high（§五）。

    非 explicit statement → 返回 None（§四 Insert/Update/**Discard**：不写 active）。
    写前 supersede 旧 active 同 content（§六）。
    """
    entry = _policy.extract_preference(text)
    if entry is None:
        return None
    content = entry.value
    mm = MemoryManager()
    try:
        await mm.supersede_stable_preference(str(user_id), content)
    except Exception as exc:
        logger.warning("supersede failed: %s", exc)
    return await mm.remember_preference(
        user_id=str(user_id), content=content, memory_type="stable_preference",
        importance=0.8, source=source,
        scope=MemoryScope.USER.value,
        status=MemoryStatus.ACTIVE.value,
        confidence=CONFIDENCE_EXPLICIT_STATEMENT.value,
    )
