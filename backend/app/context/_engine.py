"""Conversation Context Engine —— 旧 `app/context.py` 内容迁入（P3 Task 4）。

P3 plan §2.5 + review P0 #1 决议：
- 旧 `backend/app/context.py`（230 行）三类职责拆分：
  · Conversation Engine（sync 纯函数 + LLM 压缩）保留在本模块
  · Runtime storage glue → `_prepare_conversation_context` async helper（本模块）
  · Memory 落库增强 → `_save_l3_facts` async helper（本模块）
- **本模块不依赖 runtime**；外部 import 通过 `app.context` facade（`__init__.py`）走通

P4 决议：本模块整体搬到 `backend/app/memory/conversation.py`，runtime 接口不变。

`_save_l3_facts` 是 **Legacy Conversation/Memory Glue**：仍直接 `from
app.infra.memory import mem0_extractor / UserMemory`。P4 移除 context 包对
`infra.memory` 的直接依赖，Memory 写入归 Memory Manager。
"""
from __future__ import annotations

import logging

from app.llm import call_llm
from app.utils.text import safe_json_parse

logger = logging.getLogger(__name__)

# --- 关键常量 ---------------------------------------------------------------
RECENT_WINDOW = 10        # L1：保留最近 N 条原始消息
COMPRESS_BATCH = 10       # 每 M 条超出窗口的消息触发一次压缩
L2_MAX_CHARS = 800        # L2 digest 硬上限
L2_5_MAX_CHARS = 400      # L2.5 归档硬上限
L2_ARCHIVE_INTERVAL = 5   # 每 N 次 L2 重写归档一次 L2.5


def format_messages(messages: list[dict]) -> str:
    """把消息列表渲染成 `role: content` 文本，跳过空内容。"""
    lines = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{m.get('role', 'user')}: {content}")
    return "\n".join(lines)


def format_context_block(conversation_context: str) -> str:
    """把上下文包成 prompt 块，并显式声明其为「历史参考」。

    防丢失/防漂移的关键护栏：压缩摘要（L2）与召回的文本事实都可能有损，
    但**表名/列名/字段类型的权威来源永远是 prompt 里实时来自数据库的
    「可用表结构」**。此声明让模型在两者冲突时以 schema 为准，避免把
    int 列误记成 string 之类的类型漂移。三处注入（_plan/_generate_sql/
    需求解析）统一复用此函数，护栏文案单一来源。
    """
    return (
        "<对话上下文（历史参考；表名/列名/字段类型以下方「可用表结构」为准）>\n"
        f"{conversation_context}\n</对话上下文>"
    )


def archive_to_l2_5(summary: str) -> str:
    """L2.5：从当前摘要归档一段长期脉络，硬截断到上限。"""
    return (summary or "")[:L2_5_MAX_CHARS]


def compress_and_extract(old_digest: str | None, batch_messages: list[dict]) -> dict:
    """单次 LLM 调用：把「旧摘要 + 新批次」压缩成新摘要，并顺带抽取 L3 结构化事实。

    返回 ``{"summary", "extracted_schemas", "extracted_preferences"}``。
    summary 是**替换**旧摘要（不是追加），严格 ≤ L2_MAX_CHARS。
    """
    batch_text = format_messages(batch_messages)
    prompt = f"""你在维护一段对话的滚动摘要。把「旧摘要」和「最新对话」融合成一份新摘要，
并抽取其中稳定的结构化事实。

旧摘要（{len(old_digest or '')} 字）：
{old_digest or '（无）'}

最新对话（{len(batch_messages)} 条）：
{batch_text}

只输出 JSON，禁止解释、禁止 markdown：
{{
  "summary": "融合新旧信息的叙事摘要，不超过 {L2_MAX_CHARS} 字；只保留话题脉络/用户反馈/决策背景，不含具体字段名和数值",
  "extracted_schemas": [
    {{"type": "field_mapping", "user_term": "销售额", "db_field": "total_amount", "table": "fact_sales"}},
    {{"type": "calculation", "user_term": "环比", "sql_expression": "(v-LAG(v))/LAG(v)*100"}}
  ],
  "extracted_preferences": ["用户要求华东华南分开展示", "用户偏好柱状图"]
}}

要求：
1. summary 是替换旧摘要，不是追加，严格不超过 {L2_MAX_CHARS} 字。
2. extracted_schemas 只提取新出现或变更的字段映射/计算口径。
3. extracted_preferences 只提取明确、稳定的用户偏好指令。"""

    raw = call_llm(prompt, max_tokens=1000)
    result = safe_json_parse(raw)
    if not isinstance(result, dict):
        result = {}
    summary = str(result.get("summary") or "")[:L2_MAX_CHARS]
    schemas = result.get("extracted_schemas") or []
    preferences = result.get("extracted_preferences") or []
    return {
        "summary": summary,
        "extracted_schemas": [s for s in schemas if isinstance(s, dict)],
        "extracted_preferences": [str(p) for p in preferences if p],
    }


def build_context(
    *,
    messages: list[dict],
    digest: str | None = None,
    digest_msg_count: int = 0,
    digest_version: int = 0,
    mid_digest: str | None = None,
) -> tuple[str, dict, list[dict]]:
    """从对话历史构建注入 LLM 的上下文字符串。

    返回 ``(context_str, updates, compressed_batch)``：
      - context_str   前置进 LLM prompt 的上下文（<长期脉络>/<对话摘要>/<最新对话>）。
      - updates       需回写 agent.session 的 digest 字段（未触发压缩时为空 dict），
                      含本次抽取的 extracted_schemas / extracted_preferences（供 L3 落库）。
      - compressed_batch  本次被压缩的消息批次（未压缩时为空），供异步层做 mem0 增强。

    压缩触发：消息总数超过 RECENT_WINDOW + COMPRESS_BATCH，且自上次压缩后又累积了
    新批次（digest_msg_count < total - RECENT_WINDOW）。压缩为覆盖重写，绝不追加。
    """
    total = len(messages)
    updates: dict = {}
    compressed_batch: list[dict] = []

    # 消息还不多：无需压缩，原始输出即可
    if total <= RECENT_WINDOW + COMPRESS_BATCH:
        return format_messages(messages), updates, compressed_batch

    recent = messages[-RECENT_WINDOW:]
    old_count = total - RECENT_WINDOW

    if digest_msg_count < old_count:
        batch = messages[digest_msg_count:old_count]
        result = compress_and_extract(digest, batch)
        digest = result["summary"]            # 覆盖，不是追加
        digest_msg_count = old_count
        digest_version += 1
        updates = {
            "digest": digest,
            "digest_msg_count": digest_msg_count,
            "digest_version": digest_version,
            "extracted_schemas": result["extracted_schemas"],
            "extracted_preferences": result["extracted_preferences"],
        }
        compressed_batch = batch
        # 每 N 次重写归档一次 L2.5
        if digest_version % L2_ARCHIVE_INTERVAL == 0:
            mid_digest = archive_to_l2_5(digest)
            updates["mid_digest"] = mid_digest

    parts = []
    if mid_digest:
        parts.append(f"<长期脉络>\n{mid_digest}\n</长期脉络>")
    if digest:
        parts.append(f"<对话摘要>\n{digest}\n</对话摘要>")
    parts.append(f"<最新对话>\n{format_messages(recent)}\n</最新对话>")
    return "\n\n".join(parts), updates, compressed_batch


# --- async helpers（私有，前缀 `_`） -----------------------------------------


async def _prepare_conversation_context(session_id: str, user_id: int | str) -> str:
    """会话级 conversation context async glue（从旧 `build_session_context` 实质抽出）。

    取消息 + 当前 digest 状态 → `build_context` → 回写 digest（L2/L2.5）→ 把抽取的
    结构化事实写进 L3（含 mem0 增强）。返回前置进 LLM prompt 的上下文字符串。
    任何存储失败都降级为空上下文，绝不拖垮主查询链路。

    **不**调 MemoryManager.recall —— 那是 ContextRuntime 新 API 的职责。
    """
    from app.infra.checkpoint.session import session_manager
    from app.infra.conversation.repository import get_messages

    messages = await get_messages(session_id, int(user_id))
    state = await session_manager.get_context_state(session_id)
    context, updates, compressed_batch = build_context(
        messages=messages,
        digest=state["digest"],
        digest_msg_count=state["digest_msg_count"],
        digest_version=state["digest_version"],
        mid_digest=state["mid_digest"],
    )
    if updates:
        await session_manager.save_context_state(session_id, updates)
        await _save_l3_facts(user_id, updates, compressed_batch)
    return context


async def _save_l3_facts(user_id: int | str, updates: dict, compressed_batch: list[dict]) -> None:
    """**Legacy Conversation/Memory Glue**（review #9 决议）。

    把压缩抽取的结构化事实写进 L3（memory.semantic_entry）。mem0 增强可选。
    去重由 UserMemory.save（相同 user_id+content 递增 access_count）天然处理；
    这里再按内容做一次保序去重，减少无谓写入。

    **P4 removes this dependency from context package**：Memory 写入归 Memory Manager；
    本 helper 整体搬到 `backend/app/memory/conversation.py`。
    """
    from app.infra.memory import mem0_extractor
    from app.infra.memory.user_memory import UserMemory

    facts: list[str] = []
    for s in updates.get("extracted_schemas") or []:
        if isinstance(s, dict) and s:
            facts.append(str(s))
    facts += [str(p) for p in updates.get("extracted_preferences") or [] if p]

    if compressed_batch:
        try:
            facts += await mem0_extractor.extract_facts(format_messages(compressed_batch), user_id)
        except Exception as exc:
            logger.warning("mem0 augmentation failed: %s", exc)

    if not facts:
        return
    um = UserMemory()
    for fact in dict.fromkeys(facts):  # 保序去重
        try:
            await um.save(
                user_id=user_id, content=fact, memory_type="insight",
                importance_score=0.5, source="context_compress",
            )
        except Exception as exc:
            logger.warning("save L3 fact failed: %s", exc)
