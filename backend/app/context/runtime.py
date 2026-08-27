"""ContextRuntime 新 API（P3 Task 5）。

P3 plan §2.2 + review v1 钉住：
- 5 步编排：resolver.resolve → policy.decide → _engine
  ._prepare_conversation_context → MemoryManager.recall 1:1 包装 RecallItem
  → assembler.assemble → ContextBundle
- **P3 不接入现役 graph**：graph 节点仍走 `app.context.build_session_context`
  facade 兼容路径；本 API 通过 contract test 验证；P4 选择性迁移调用方
"""
from __future__ import annotations

from app.context import _engine as _engine_module
from app.context._engine import (
    _prepare_conversation_context as _engine_prepare_conversation_context,
)
from app.context.assembler import ContextAssembler, ContextBundle, RecallItem
from app.context.decision import ContextDecisionPolicy, LegacyFallbackPolicy
from app.context.policy import AgentContextPolicy, ContextPolicyResolver
from app.infra.memory.memory_manager import MemoryManager


class ContextRuntime:
    """伞形 plan §六 Recall Before Agent 统一入口（新 API）。"""

    def __init__(
        self,
        policy: ContextDecisionPolicy | None = None,
        resolver: ContextPolicyResolver | None = None,
        assembler: ContextAssembler | None = None,
    ):
        self._policy: ContextDecisionPolicy = policy or LegacyFallbackPolicy()
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
    ) -> ContextBundle:
        # Step 1：解析 agent_policy
        agent_policy: AgentContextPolicy = self._resolver.resolve(agent)
        # Step 2：recall 决策
        decision = self._policy.decide(
            query=query,
            agent_policy=agent_policy,
            session_state=state_dict or {},
        )
        # Step 3：conversation context（_engine 私有 async helper；**不**通过 facade
        # 避免循环；与现 build_session_context 实质等价）
        conversation_context = await _engine_prepare_conversation_context(
            session_id, user_id,
        )
        # Step 4：memory recall；1:1 包装 string 为 RecallItem（review P0 #2）
        recall_items: list[RecallItem] = []
        if decision.semantic or decision.query:
            text = await MemoryManager().recall(
                query,
                user_id,
                top_k_queries=decision.top_k_queries,
                top_k_preferences=decision.top_k_preferences,
            )
            if text:
                recall_items = [
                    RecallItem(raw_text=text, source="legacy_memory_manager"),
                ]
        # Step 5：assemble
        return self._assembler.assemble(
            conversation_context=conversation_context,
            recall_items=recall_items,
            agent_policy=agent_policy,
        )


context_runtime = ContextRuntime()
