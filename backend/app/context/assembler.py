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

import os
from typing import Literal, NotRequired, TypedDict

from app.context.policy import AgentContextPolicy
from app.state.checkpoint_adapter import CURRENT_SCHEMA_VERSION


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


def _apply_token_budget(text: str, remaining_token_budget: int | None = None) -> str:
    """按 P4C_ASSEMBLER_TOKEN_BUDGET env 截断. P4c Task 4 真实装.
    P4c post-review F2: 真实 effective budget = min(remaining_token_budget, configured).
    Token Budget 控制 assembled_context 总长度 (char count 估算, 1 token ≈ 3 chars).
    - remaining_token_budget=None: 仅走 configured budget (向后兼容 P3 不裁剪)
    - remaining_token_budget=<int>: effective = min(remaining, configured)
      (避免超出 LLM 上下文预算)

    默认 4000 tokens (= 12000 chars). Env 不设时等同 P4a 行为.
    """
    budget_tokens = int(os.getenv("P4C_ASSEMBLER_TOKEN_BUDGET", _DEFAULT_TOKEN_BUDGET))
    if remaining_token_budget is not None:
        budget_tokens = min(budget_tokens, remaining_token_budget)
    char_cap = budget_tokens * _CHARS_PER_TOKEN
    if len(text) <= char_cap:
        return text
    return text[:char_cap]


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
        # P3 Assembly: 拼接 assembled_context; 不调 format_context_block (caller 决定)
        if recall_block and conversation_context:
            assembled = f"{recall_block}\n\n{conversation_context}"
        else:
            assembled = recall_block or conversation_context
        # P4c Task 4: 真实 Budget 截断 + P4c post-review F2: 接受 remaining_token_budget
        assembled = _apply_token_budget(assembled, remaining_token_budget)
        return ContextBundle(
            conversation_context=conversation_context,
            recall_items=kept,
            assembled_context=assembled,
            agent_policy=agent_policy.value,
            schema_version=CURRENT_SCHEMA_VERSION,
        )
