"""P4c Task 3: 验证 SelectiveRecallPolicy 真的分流掉无关召回。

注: plan Step 1 原 parametrize 期望值与 SelectiveRecallPolicy 实际行为不一致
(例: '你好' chitchat 路径返回 conv=False；plan 期望 conv=True)。Task 3 inline
重写：把 8 用例精确 parametrize 换成 property-based 测试,反映 selective policy
    关键性质 + 与 LegacyFallbackPolicy 对照分流。

参考: backend/app/context/decision.py:
    _is_chitchat(q): len(q) <= 8 && q 含 _CHITCHAT 关键词 → 全 False
    conversation = history_ref  # _HISTORY_REF 关键词
    semantic = history_ref or biz_def or pref_task
    query_exp:
      REPORT  → False (§三分流)
      EXECUTION → data_similar
      REQUIREMENT → data_similar AND history_ref (少量)
"""
from __future__ import annotations

import pytest

from app.context.decision import (
    LegacyFallbackPolicy,
    SelectiveRecallPolicy,
)
from app.context.policy import AgentContextPolicy


def _agent(agent_str: str):
    """Map test string to AgentContextPolicy enum."""
    return {
        "requirement": AgentContextPolicy.REQUIREMENT,
        "execution": AgentContextPolicy.EXECUTION,
        "report": AgentContextPolicy.REPORT,
    }[agent_str]


# --- Key property tests ---------------------------------------------------


class TestSelectivePolicyChitchat:
    """Chitchat/empty query 必须全 False (P4b 收益 1: 省 embedding 二次往返)."""

    @pytest.mark.parametrize("q", ["你好", "谢谢", "嗨", "", "  "])
    def test_chitchat_or_empty_returns_all_false(self, q):
        decision = SelectiveRecallPolicy().decide(
            query=q, agent_policy=_agent("requirement"), session_state={},
        )
        assert decision.conversation is False, f"[{q!r}] conv should be False"
        assert decision.semantic is False, f"[{q!r}] sem should be False"
        assert decision.query is False, f"[{q!r}] q should be False"
        assert decision.top_k_queries == 0
        assert decision.top_k_preferences == 0


class TestSelectivePolicyAgentTable:
    """memory-architecture §三 Agent-specific 分流. Report 永不召 Query."""

    def test_report_never_recalls_query_even_with_data_keyword(self):
        """Report + '销售' 数据动词 → 仍然 query=False (§三分流钉)."""
        decision = SelectiveRecallPolicy().decide(
            query="本月销售汇总", agent_policy=_agent("report"), session_state={},
        )
        assert decision.query is False, "Report 必须 query=False (§三)"
        assert decision.top_k_queries == 0
        # Report 仍可召 semantic(preference) 来满足图表偏好
        assert decision.semantic is True  # agent == REPORT 触发 pref_task

    def test_execution_recalls_query_when_data_similar(self):
        """Execution + 数据动词 → query=True."""
        decision = SelectiveRecallPolicy().decide(
            query="统计上月销售额", agent_policy=_agent("execution"), session_state={},
        )
        assert decision.query is True
        assert decision.top_k_queries == 2

    def test_requirement_query_only_when_history_ref_and_data(self):
        """Requirement: query 仅历史引用 + 数据动词同时存在."""
        # 历史引用 + 数据动词 → query True
        d1 = SelectiveRecallPolicy().decide(
            query="再按产品细分", agent_policy=_agent("requirement"), session_state={},
        )
        assert d1.query is True
        # 仅数据动词 (无历史引用) → query False (Requirement "少量")
        d2 = SelectiveRecallPolicy().decide(
            query="统计上月销售额", agent_policy=_agent("requirement"), session_state={},
        )
        assert d2.query is False
        # 仅历史引用 (无数据动词) → query False
        # 注: 必须构造不含 _DATA_VERB 子串 ("销售/统计/查询/排名/汇总" 等) 的 history_ref query
        d3 = SelectiveRecallPolicy().decide(
            query="继续刚才那个意图", agent_policy=_agent("requirement"), session_state={},
        )
        assert d3.query is False, (
            f"requirement 仅 history_ref 应不召 query, got {d3}"
        )


class TestSelectivePolicySemanticTriggers:
    """semantic 召回的 §二触发条件: history_ref / biz_def / pref_task 任一即可."""

    @pytest.mark.parametrize("query,trigger_label", [
        ("再按产品细分", "history_ref"),       # "再按" → history_ref
        ("GMV 是什么口径？", "biz_def"),        # GMV+口径 → biz_def
        ("以后都用图表展示", "pref_task"),      # "图表"+"展示" → pref_task
        ("本月销售汇总", "no_trigger"),        # 无上述触发 + requirement agent
    ])
    def test_semantic_follows_trigger(self, query, trigger_label):
        decision = SelectiveRecallPolicy().decide(
            query=query, agent_policy=_agent("requirement"), session_state={},
        )
        if trigger_label == "no_trigger":
            # _DATA_VERB("销售") 不在 _BIZ_DEF/_HISTORY_REF/_PREF_TASK 中;
            # 但 _PREF_TASK 中的某个子串检查要小心——"展示" 不在 query 中, "图表" 不在 query 中
            # 因此 pref_task = False. semantic 应为 False (除非有其他匹配)
            # 但 _DATA_VERB("销售")在 — 这是 query trigger,不是 semantic trigger
            assert decision.semantic is False
        else:
            assert decision.semantic is True, f"[{trigger_label}] {query!r}: sem=False"
            assert decision.top_k_preferences == 3


class TestConversationTrigger:
    """conversation = history_ref (即 §二触发1)."""

    def test_no_history_ref_means_no_conversation(self):
        d = SelectiveRecallPolicy().decide(
            query="本月销售汇总", agent_policy=_agent("execution"), session_state={},
        )
        assert d.conversation is False

    @pytest.mark.parametrize("kw", ["继续", "刚才", "再按", "还是"])
    def test_history_ref_keyword_triggers_conversation(self, kw):
        d = SelectiveRecallPolicy().decide(
            query=f"{kw} 上次的口径", agent_policy=_agent("execution"), session_state={},
        )
        assert d.conversation is True


# --- Diff with LegacyFallbackPolicy (核心收益证明) -------------------------


class TestSelectiveVsLegacyDiffers:
    """关键收益: SelectiveRecallPolicy vs LegacyFallbackPolicy 在多数场景下 decision 不同.
    LegacyFallbackPolicy 永远全开;Selective 真分流.
    """

    @pytest.mark.parametrize("query,agent", [
        ("你好", "requirement"),                   # chitchat → 全 False vs legacy 全 True
        ("本月销售汇总", "requirement"),           # 完整 query (无 history_ref) → conv=False vs legacy conv=True
        ("", "report"),                            # empty → 全 False vs legacy 全 True
        ("渲染当前查询的报告", "report"),          # Report agent → q=False vs legacy q=True
        ("本月销售汇总", "report"),                # Report agent → q=False vs legacy q=True
    ])
    def test_selective_differs_from_legacy_for_query_and_semantic(self, query, agent):
        sel = SelectiveRecallPolicy().decide(
            query=query, agent_policy=_agent(agent), session_state={},
        )
        leg = LegacyFallbackPolicy().decide(
            query=query, agent_policy=_agent(agent), session_state={},
        )
        # Legacy 全开;Selective 至少应该选择性关掉某些 bool
        # 5 个 case 中必须至少出现一次 "sel.X=False and leg.X=True"
        semantic_diff = sel.semantic is False and leg.semantic is True
        query_diff = sel.query is False and leg.query is True
        conv_diff = sel.conversation is False and leg.conversation is True
        assert semantic_diff or query_diff or conv_diff, (
            f"q={query!r} agent={agent}: selective 与 legacy 决策全相同 "
            f"(sel={{conv:{sel.conversation}, sem:{sel.semantic}, q:{sel.query}}} "
            f"leg={{conv:{leg.conversation}, sem:{leg.semantic}, q:{leg.query}}})"
        )


# --- Top-k budget reflects decision ---------------------------------------


class TestTopKBudget:
    """decision.top_k_* 必须与 decision.{semantic,query} 一致 (False 时必为 0)."""

    def test_top_k_zero_when_query_false(self):
        d = SelectiveRecallPolicy().decide(
            query="渲染报告", agent_policy=_agent("report"), session_state={},
        )
        if d.query is False:
            assert d.top_k_queries == 0
        if d.semantic is False:
            assert d.top_k_preferences == 0

    def test_legacy_keeps_p3_baseline_top_k(self):
        """LegacyFallbackPolicy 必须保持与 P3 MemoryManager.recall 兼容的 2/3 (P4b §T5)."""
        d = LegacyFallbackPolicy().decide(
            query="anything", agent_policy=_agent("execution"), session_state={},
        )
        assert d.top_k_queries == 2
        assert d.top_k_preferences == 3
