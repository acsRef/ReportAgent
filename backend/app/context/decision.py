"""Context decision Protocol + RecallDecision + LegacyFallbackPolicy + SelectiveRecallPolicy。

P3 §2.2 + P4b §二/§三：
- ContextDecisionPolicy Protocol 入参带 agent_policy（review P1 #4 闭合两层 abstraction）
- LegacyFallbackPolicy 全开 + top_k 与现 MemoryManager.recall 一字不变（2/3）
- SelectiveRecallPolicy（P4b）：memory-architecture §二四触发条件 + §三 agent 表分流，
  纯规则无 LLM。**P4b 仅 contract test 注入验证；graph caller 翻转留 P4c。**
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

    agent_policy 入参暂时忽略（fallback 不分流）；SelectiveRecallPolicy 按
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


# --- SelectiveRecallPolicy（P4b，§二/§三） ----------------------------------

_CHITCHAT = ("你好", "您好", "谢谢", "多谢", "再见", "拜拜", "哈哈", "在吗", "早", "嗨")
_HISTORY_REF = ("继续", "刚才", "上次", "之前", "再按", "那个", "同样", "还是", "接着")
_BIZ_DEF = ("GMV", "DAU", "MAU", "ARPU", "口径", "定义", "环比", "同比", "毛利", "净利", "客单价")
_DATA_VERB = ("统计", "查询", "查", "排名", "排行", "趋势", "销售", "金额", "数量",
              "占比", "对比", "汇总", "按季度", "按月", "按区域", "按产品", "分布")
_PREF_TASK = ("图表", "报告", "展示", "显示", "格式", "可视化", "画", "生成报告")


class SelectiveRecallPolicy:
    """§二 Selective Recall 四触发条件 + §三 Agent-specific Policy 表，纯规则。

    默认不自动召回全部长期记忆。触发：
      1 历史引用（"继续/刚才/再按…细分"）→ conversation
      2 长期偏好影响当前任务（报告/图表）→ semantic(preference)
      3 业务定义影响理解（GMV/口径…）→ semantic
      4 Query Experience 与当前查询高相似（数据分析动词）→ query
    不召回：纯闲聊、query 已完整且与历史无关。
    §三 表封顶：Report 永不召回 Query Experience；Requirement 的 query 为「少量」
    （仅历史引用延续时才召）。
    """

    def decide(
        self,
        *,
        query: str,
        agent_policy: AgentContextPolicy,
        session_state: dict,
    ) -> RecallDecision:
        q = (query or "").strip()
        if not q or self._is_chitchat(q):
            return RecallDecision(
                conversation=False, semantic=False, query=False,
                top_k_queries=0, top_k_preferences=0, rationale="chitchat/empty",
            )

        history_ref = any(k in q for k in _HISTORY_REF)
        biz_def = any(k in q for k in _BIZ_DEF)
        pref_task = any(k in q for k in _PREF_TASK) or agent_policy == AgentContextPolicy.REPORT
        data_similar = any(k in q for k in _DATA_VERB)

        conversation = history_ref  # §二触发1；query 已完整且不延续历史 → 不召

        semantic = history_ref or biz_def or pref_task

        # §三 表 + §二触发4
        if agent_policy == AgentContextPolicy.REPORT:
            query_exp = False                       # Report ❌ Query
            top_k_queries = 0
        elif agent_policy == AgentContextPolicy.EXECUTION:
            query_exp = data_similar                # Execution ✅ Query
            top_k_queries = 2
        else:  # REQUIREMENT
            query_exp = data_similar and history_ref  # Requirement 少量
            top_k_queries = 1 if query_exp else 0

        return RecallDecision(
            conversation=conversation,
            semantic=semantic,
            query=query_exp,
            top_k_queries=top_k_queries,
            top_k_preferences=3 if semantic else 0,
            rationale="selective",
        )

    @staticmethod
    def _is_chitchat(q: str) -> bool:
        return len(q) <= 8 and any(k in q for k in _CHITCHAT)
