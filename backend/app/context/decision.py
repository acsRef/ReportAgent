"""Context decision Protocol + RecallDecision + LegacyFallbackPolicy（P3 Task 5）。

P3 plan §2.2 + review P0 #2 + P1 #4 钉住：
- ContextDecisionPolicy Protocol 入参带 agent_policy（review P1 #4 闭合两层 abstraction）
- LegacyFallbackPolicy 全开 + top_k 与现 MemoryManager.recall 一字不变（2/3）
- 不调任何 Memory API：runtime step 4 自己调 + 包装 RecallItem
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from app.context.policy import AgentContextPolicy


class RecallDecision(BaseModel):
    conversation: bool = True
    semantic: bool = True
    query: bool = True
    top_k_queries: int = 2
    top_k_preferences: int = 3
    rationale: str = ""


class ContextDecisionPolicy(Protocol):
    def decide(
        self,
        *,
        query: str,
        agent_policy: AgentContextPolicy,
        session_state: dict,
    ) -> RecallDecision: ...


class LegacyFallbackPolicy:
    """P3 默认 fallback：等价现 MemoryManager.recall 全量召回。

    agent_policy 入参暂时忽略（fallback 不分流）；P4 SelectiveRecallPolicy 按
    agent_policy 分流。
    """

    def decide(
        self,
        *,
        query: str,
        agent_policy: AgentContextPolicy,
        session_state: dict,
    ) -> RecallDecision:
        return RecallDecision(
            conversation=True,
            semantic=True,
            query=True,
            top_k_queries=2,
            top_k_preferences=3,
            rationale="legacy fallback",
        )
