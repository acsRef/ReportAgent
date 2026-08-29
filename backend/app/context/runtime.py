"""ContextRuntime 新 API（P3 Task 5 + P4b F3/F11 收口）。

P3 plan §2.2 + review v1 钉住：
- 5 步编排：resolver.resolve → policy.decide → _engine
  ._prepare_conversation_context → MemoryManager.recall 1:1 包装 RecallItem
  → assembler.assemble → ContextBundle
- **P3 不接入现役 graph**：graph 节点仍走 `app.context.build_session_context`
  facade 兼容路径；本 API 通过 contract test 验证；P4 选择性迁移调用方

P4b F3：Memory recall 改走 domain views（app.memory.{semantic,query}）；context 不再
直接 import infra.memory.memory_manager（plan §F3）。
P4b F11：SelectiveRecallPolicy.decision.conversation 不再是死字段；False 时跳过
conversation 召回（plan §F11）。
"""
from __future__ import annotations

from app.context.assembler import ContextAssembler, ContextBundle, RecallItem
from app.context.decision import (
    ContextDecisionPolicy,
    LegacyFallbackPolicy,
    SelectiveRecallPolicy,
)
from app.context.policy import AgentContextPolicy, ContextPolicyResolver
# P4a：conversation 引擎迁入 app.memory（domain 层）；context 经 memory 域调用
from app.memory.conversation import prepare_conversation_context
# F3：recall 走 domain views（infra.memory.memory_manager 由 view 内部委托），
# 不再由 ContextRuntime 直接 import。
from app.memory import query as query_memory, semantic as semantic_memory


class ContextRuntime:
    """伞形 plan §六 Recall Before Agent 统一入口（新 API）。"""

    def __init__(
        self,
        policy: ContextDecisionPolicy | None = None,
        resolver: ContextPolicyResolver | None = None,
        assembler: ContextAssembler | None = None,
    ):
        self._policy: ContextDecisionPolicy = policy or SelectiveRecallPolicy()  # P4c post-review F1: 默认必须 SelectiveRecallPolicy（plan 目标 "主图真触发 selective"）；LegacyFallbackPolicy 仅显式兼容
        self._resolver: ContextPolicyResolver = resolver or ContextPolicyResolver()
        self._assembler: ContextAssembler = assembler or ContextAssembler()

    async def build(
        self,
        *,
        session_id: str,
        user_id: int,
        query: str,
        agent: str,
        state_dict: dict | None = None,
        remaining_token_budget: int | None = None,  # P4c post-review F2: 透传给 assembler
    ) -> ContextBundle:
        # Step 1：解析 agent_policy
        agent_policy: AgentContextPolicy = self._resolver.resolve(agent)
        # Step 2：recall 决策
        decision = self._policy.decide(
            query=query,
            agent_policy=agent_policy,
            session_state=state_dict or {},
        )
        # Step 3：conversation context（F11：SelectiveRecallPolicy.decision.conversation
        # 不再是死字段；False 时跳过——纯闲聊 / 已完整 query 等场景不浪费一次 DB 往返）。
        conversation_context = ""
        if decision.conversation:
            conversation_context = await prepare_conversation_context(
                session_id, user_id,
            )
        # Step 4：memory recall（P4b 结构化 + F3 经 domain views）
        recall_items: list[RecallItem] = []
        if decision.semantic:
            recall_items.extend(await semantic_memory.recall_structured(
                query,
                str(user_id),
                top_k_preferences=decision.top_k_preferences,
            ))
        if decision.query:
            recall_items.extend(await query_memory.recall_structured(
                query,
                str(user_id),
                top_k_queries=decision.top_k_queries,
            ))
        # Step 5：assemble (P4c post-review F2: 透传 remaining_token_budget)
        return self._assembler.assemble(
            conversation_context=conversation_context,
            recall_items=recall_items,
            agent_policy=agent_policy,
            remaining_token_budget=remaining_token_budget,
        )


context_runtime = ContextRuntime()
