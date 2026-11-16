"""Context assembler + RecallItem + ContextBundle (P3 Task 5 + P4c Task 4).

P3 plan §2.2 + review P0 #2 钉住:
- RecallItem 1:1 包装 MemoryManager.recall 返回的 string, **不**解析
- ContextBundle 字段语义: conversation_context 透传输入(不含 format_context_block
  包裹) + recall_items + assembled_context + agent_policy + schema_version
- ContextAssembler 纯组装, 不依赖任何外部 API
- Pipeline: Filter → Conflict Resolution (P3 简化版按 memory-architecture §七
  固定序拼接) → Budget (P3 留接口与默认不裁剪) → Assembly

P4c Task 4 (real装):
- Filter 加 dedup by (source, ref_id), 保留 score 最高 + drop empty
- Conflict Resolution 加 §七 kind 排序: query > semantic > preference
- Budget 真实装: 按 P4C_ASSEMBLER_TOKEN_BUDGET env (default 4000 tokens ≈ 12000 chars)
- 公共签名 ContextBundle 不变, 既有 contract test 必须不破
"""
from __future__ import annotations

import logging
import os
from typing import Literal, NotRequired, TypedDict

from app.context.policy import AgentContextPolicy
from app.state.checkpoint_adapter import CURRENT_SCHEMA_VERSION

logger = logging.getLogger(__name__)


class RecallItem(TypedDict):
    """P4b 扩:kind/score/ref_id 供 structured recall 与 P4c 冲突消解/预算用.
    raw_text + source 保留(兼容 P3 1:1 包装 + legacy join)."""
    raw_text: str
    source: Literal[
        "legacy_memory_manager", "memory_query", "memory_semantic", "memory_preference",
    ]
    kind: NotRequired[str]        # "query"/"semantic"/"preference"
    score: NotRequired[float]
    ref_id: NotRequired[int]


class ContextBundle(TypedDict):
    conversation_context: str
    recall_items: list[RecallItem]
    assembled_context: str
    agent_policy: str
    schema_version: str


# P4c Task 4: §七 kind 排序固定序 (query > semantic > preference).
_KIND_ORDER: dict[str, int] = {"query": 0, "semantic": 1, "preference": 2}

# P4c Task 4: Token Budget 默认值. 1 token ≈ 3 chars (粗估).
_DEFAULT_TOKEN_BUDGET = 4000
_CHARS_PER_TOKEN = 3


def _filter_and_dedup_and_sort(items: list[RecallItem]) -> list[RecallItem]:
    """Filter 三件事: drop empty + dedup by (source, ref_id) keep highest score +
    §七 kind 排序. P4c Task 4 真实装.

    Returns 过滤/去重/排序后的 list (顺序按 §七).
    """
    by_key: dict[tuple, RecallItem] = {}
    for it in items:
        if not it.get("raw_text"):
            continue  # Filter: drop empty
        key = (it.get("source"), it.get("ref_id"))
        cur = by_key.get(key)
        if cur is None or it.get("score", 0.0) > cur.get("score", 0.0):
            by_key[key] = it
    return sorted(
        by_key.values(),
        key=lambda x: _KIND_ORDER.get(x.get("kind", ""), 99),
    )


def _budget_chars(remaining_token_budget: int | None = None) -> int:
    """effective 预算（chars）：min(remaining, configured) × 3。"""
    budget_tokens = int(os.getenv("P4C_ASSEMBLER_TOKEN_BUDGET", _DEFAULT_TOKEN_BUDGET))
    if remaining_token_budget is not None:
        budget_tokens = min(budget_tokens, remaining_token_budget)
    return max(0, budget_tokens * _CHARS_PER_TOKEN)


def _keep_tail_lines(text: str, char_cap: int) -> str:
    """**保尾**：从最新一行往回收，丢最老的整行。

    2026-11-16 修正（plan compaction-dsh-alignment 缺陷 ①）：原实现是 `text[:cap]`，
    而对话按时间正序排在尾部 → 一超预算就「留最老、丢最新」，正好反了。改为整行粒度，
    既不切断最新内容，也不会把一个金额从中间切开。
    """
    if char_cap <= 0 or not text:
        return "" if char_cap <= 0 else text
    if len(text) <= char_cap:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        cost = len(line) + (1 if kept else 0)
        if kept and total + cost > char_cap:
            break
        if not kept and len(line) > char_cap:
            # 单行自身超预算：只能留该行尾部（最新内容），并告警。
            logger.warning("context budget: single line %s chars over cap %s, kept tail", len(line), char_cap)
            return line[-char_cap:]
        kept.append(line)
        total += cost
    dropped = len(lines) - len(kept)
    if dropped:
        logger.warning("context budget: dropped %s oldest lines (cap %s chars)", dropped, char_cap)
    return "\n".join(reversed(kept))


def _drop_tail_lines(text: str, keep_chars: int) -> str:
    """召回块按 §七 优先级**从尾部丢**（`preference` 先丢，`query` 最后丢）。"""
    if not text:
        return ""
    if keep_chars <= 0:
        return ""
    if len(text) <= keep_chars:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    total = 0
    for line in lines:
        cost = len(line) + (1 if kept else 0)
        if total + cost > keep_chars:
            if not kept and keep_chars > 0:
                kept.append(line[:keep_chars])  # 单条自身超限：截该条自身，不越界
            break
        kept.append(line)
        total += cost
    return "\n".join(kept)


def _fit_budget(
    *,
    conversation_context: str,
    recall_block: str,
    char_cap: int,
) -> tuple[str, str]:
    """把两块内容压进预算：**对话保尾**，召回按优先级从尾部丢。

    预算分配：召回最多占 1/3（它可丢条目，但高优先级条目优先保留），剩下给对话；
    对话压完若还有余量，再全额给召回。空预算时两块都返回空。
    """
    if char_cap <= 0:
        return "", ""
    if len(recall_block) + len(conversation_context) + 2 <= char_cap:
        return conversation_context, recall_block

    recall_cap = min(len(recall_block), max(0, char_cap // 3))
    conversation_cap = max(0, char_cap - recall_cap - 2)
    conversation_context = _keep_tail_lines(conversation_context, conversation_cap)
    remaining = max(0, char_cap - len(conversation_context) - 2)
    recall_block = _drop_tail_lines(recall_block, remaining)
    return conversation_context, recall_block


def _apply_token_budget(text: str, remaining_token_budget: int | None = None) -> str:
    """单块文本的预算裁剪（保尾）。保留此函数供既有调用/测试使用。"""
    return _keep_tail_lines(text, _budget_chars(remaining_token_budget))


class ContextAssembler:
    def assemble(
        self,
        *,
        conversation_context: str,
        recall_items: list[RecallItem],
        agent_policy: AgentContextPolicy,
        remaining_token_budget: int | None = None,  # P4c post-review F2
    ) -> ContextBundle:
        # P4c Task 4: 真实 Filter (dedup + sort)
        kept: list[RecallItem] = _filter_and_dedup_and_sort(recall_items)
        # P3 Conflict Resolution (按 memory-architecture §七 固定序拼接;
        # recall 在前, conversation 在后); 不主动重排
        recall_block = "\n".join(it["raw_text"] for it in kept) if kept else ""
        # 2026-11-16（plan compaction-dsh-alignment 缺陷 ①）：预算裁剪改为**保尾**——
        # 对话（时间正序在尾部）不许再被 `text[:cap]` 砍掉最新几轮；召回块按优先级从尾部丢。
        conversation_context, recall_block = _fit_budget(
            conversation_context=conversation_context,
            recall_block=recall_block,
            char_cap=_budget_chars(remaining_token_budget),
        )
        # P3 Assembly: 拼接 assembled_context; 不调 format_context_block (caller 决定)
        if recall_block and conversation_context:
            assembled = f"{recall_block}\n\n{conversation_context}"
        else:
            assembled = recall_block or conversation_context
        return ContextBundle(
            conversation_context=conversation_context,
            recall_items=kept,
            assembled_context=assembled,
            agent_policy=agent_policy.value,
            schema_version=CURRENT_SCHEMA_VERSION,
        )
