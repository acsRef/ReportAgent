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

import datetime
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
from app.memory.admission import classify_scope, is_admissible
from app.memory.preferences import memory_key

logger = logging.getLogger(__name__)

_policy = MemoryPolicy()


async def remember_conversation_facts(
    user_id: int | str, updates: dict, compressed_batch: list[dict],
    *, session_id: str | None = None,
) -> None:
    """兼容入口（conversation.prepare_conversation_context 仍调它，时机不变）。

    P4b：把 LLM 压缩抽取的 schema/preference facts 路由到 `remember_inferred_facts`
    → status=candidate（§五 line 49「LLM inferred 不进 active」）。这是相对
    P3/P4a 的**行为修正**：以前当 insight/active 写并被召回，现落 candidate 不被召回。

    P4b F9：透传 `session_id` 给 `remember_inferred_facts`（plan §F9）——session-scope
    候选条目能正确归属（schema M4: session-bound preference 区分）。
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
    await remember_inferred_facts(
        user_id, list(dict.fromkeys(facts)), session_id=session_id,  # 保序去重
    )


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


def remember_explicit_preference_plan(text: str):
    """纯函数：给定一句话，返回写入参数（或 None=不写）。便于单测与日志排查。

    2026-11-09（plan memory-admission-and-consolidation）：此前只看「像不像偏好」，
    写库时硬编码 `stable_preference / scope=user / 无 expires_at` —— 结果是
    「以后都用柱状图」和「这次先按周看」生命周期完全相同，`temporary_preference`
    在生产里永远不产生。现在由 `admission.classify_scope` 决定三档。
    """
    entry = _policy.extract_preference(text)
    if entry is None:
        return None
    ok, reason = is_admissible(text)
    if not ok:
        logger.info("memory admission rejected: reason=%s text=%r", reason, text[:40])
        return None
    adm = classify_scope(text)
    expires_at = (
        datetime.datetime.now() + datetime.timedelta(hours=adm.ttl_hours)
        if adm.ttl_hours else None
    )
    return {
        "content": entry.value,
        "admission": adm,
        "expires_at": expires_at,
    }


async def remember_explicit_preference(
    user_id: int | str,
    text: str,
    *,
    source: str = "user_turn",
    session_id: str | None = None,
) -> int | None:
    """Explicit user statement → 长期库 / 工作区 / 候选（§五/§六 + 准入三档）。

    - 长期标记（以后/默认/每次）→ **`memory.long_term_memory`**（按意图槽位 UPSERT，
      同一维度只有一行，写侧不再需要 supersede 扫描）；
    - 会话标记（这次/先/暂时）→ `semantic_entry`（active + session + 24h，当轮生效）；
    - 无作用域标记 → `semantic_entry`（candidate，等离线固化攒证据）。

    非 explicit statement → 返回 None（§四 Insert/Update/**Discard**）。
    """
    plan = remember_explicit_preference_plan(text)
    if plan is None:
        return None
    adm = plan["admission"]
    content = plan["content"]
    mm = MemoryManager()
    if adm.is_long_term:
        # 长期记忆进**独立库**（plan long-term-memory-table）。同槽位唯一 →
        # 「柱状图 / 饼图」两条矛盾偏好并存从结构上消失（新值覆盖旧值，旧正文留 previous_content）。
        result = await mm.remember_long_term(
            user_id=user_id,
            memory_key=memory_key(content, adm.memory_type),
            content=content,
            memory_type=adm.memory_type,
            importance=0.8,
            source=source,
            confidence=CONFIDENCE_EXPLICIT_STATEMENT.value,
        )
        logger.info(
            "long-term memory upserted: key=%s revision=%s replaced=%s",
            result["memory_key"], result["revision"], result["replaced"],
        )
        return result["id"]
    # 会话级却没给 session_id → 存了也召不回（_scope_filter 要求 session 匹配），
    # 降级成 candidate 更诚实：不假装“已记住”。
    scope, status = adm.scope, adm.status
    if adm.scope == "session" and not session_id:
        logger.warning("session-scoped preference without session_id → candidate: %r", text[:40])
        scope, status = "user", "candidate"
    return await mm.remember_preference(
        user_id=str(user_id), content=content, memory_type=adm.memory_type,
        importance=0.8 if adm.writes_active else 0.5, source=source,
        scope=scope,
        status=status,
        confidence=CONFIDENCE_EXPLICIT_STATEMENT.value,
        session_id=session_id,
        expires_at=plan["expires_at"],
    )


def extract_explicit_preference(text: str):
    """P16.5：explicit preference 正则检测——chat 入口图前短路用（§五）。

    与 `remember_explicit_preference` 共用同一 MemoryPolicy 规则源（单一真源，
    避免「检测能命中、写入不命中」的分裂）；返回 MemoryEntry | None。
    """
    return _policy.extract_preference(text)