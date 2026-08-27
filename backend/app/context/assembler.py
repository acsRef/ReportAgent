"""Context assembler + RecallItem + ContextBundle（P3 Task 5）。

P3 plan §2.2 + review P0 #2 钉住：
- RecallItem 1:1 包装 MemoryManager.recall 返回的 string，**不**解析
- ContextBundle 字段语义：conversation_context 透传输入（不含 format_context_block
  包裹）+ recall_items + assembled_context + agent_policy + schema_version
- ContextAssembler 纯组装，不依赖任何外部 API
- Pipeline：Filter → Conflict Resolution（P3 简化版按 memory-architecture §七
  固定序拼接）→ Budget（P3 留接口与默认不裁剪）→ Assembly
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from app.context.policy import AgentContextPolicy
from app.state.checkpoint_adapter import CURRENT_SCHEMA_VERSION


class RecallItem(TypedDict):
    """P4b 扩：kind/score/ref_id 供 structured recall 与 P4c 冲突消解/预算用。
    raw_text + source 保留（兼容 P3 1:1 包装 + legacy join）。"""
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


class ContextAssembler:
    def assemble(
        self,
        *,
        conversation_context: str,
        recall_items: list[RecallItem],
        agent_policy: AgentContextPolicy,
    ) -> ContextBundle:
        # Filter：drop 空 raw_text item
        kept: list[RecallItem] = [i for i in recall_items if i.get("raw_text")]
        # Conflict Resolution（P3 简化版）：按 memory-architecture §七 固定序
        # 拼接（recall 在前，conversation 在后）；不主动重排
        recall_block = "\n".join(i["raw_text"] for i in kept) if kept else ""
        # Budget（P3 仅留接口与默认不裁剪）
        # Assembly：拼接 assembled_context；不调 format_context_block（caller 决定）
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
