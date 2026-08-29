"""P3 Task 5: ContextRuntime + Decision + Policy + Assembler 新 API 契约。

P3 plan §2.2 钉住（review v1 后）：
- runtime 5 步编排：resolver.resolve → policy.decide → _engine
  ._prepare_conversation_context → MemoryManager.recall 1:1 包装 RecallItem
  → assembler.assemble → ContextBundle
- LegacyFallbackPolicy.decide() 入参 agent_policy 暂时忽略，返回全开 plan
- ContextPolicyResolver 按前缀映射；未知 → REQUIREMENT
- ContextAssembler.assemble() 不依赖任何外部 API；**不**自动调 format_context_block
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.context.assembler import ContextAssembler, ContextBundle, RecallItem
from app.context.decision import LegacyFallbackPolicy, RecallDecision
from app.context.policy import AgentContextPolicy, ContextPolicyResolver
from app.context.runtime import ContextRuntime


# --- decision.py ------------------------------------------------------------


class TestLegacyFallbackPolicy:
    def test_returns_full_recall_decision(self):
        policy = LegacyFallbackPolicy()
        decision = policy.decide(
            query="2024 销售", agent_policy=AgentContextPolicy.REQUIREMENT,
            session_state={},
        )
        assert isinstance(decision, RecallDecision)
        assert decision.conversation is True
        assert decision.semantic is True
        assert decision.query is True
        # review P0 #2 + 现状：与现 MemoryManager.recall 默认 top_k 一字不变
        assert decision.top_k_queries == 2
        assert decision.top_k_preferences == 3

    def test_ignores_agent_policy_param(self):
        # review P1 #4 闭合：agent_policy 入参存在但 fallback 忽略
        policy = LegacyFallbackPolicy()
        d1 = policy.decide(query="x", agent_policy=AgentContextPolicy.REQUIREMENT, session_state={})
        d2 = policy.decide(query="x", agent_policy=AgentContextPolicy.EXECUTION, session_state={})
        d3 = policy.decide(query="x", agent_policy=AgentContextPolicy.REPORT, session_state={})
        assert d1 == d2 == d3  # 都返回同样全开 plan


# --- policy.py --------------------------------------------------------------


class TestContextPolicyResolver:
    @pytest.mark.parametrize("agent_name,expected", [
        ("requirement_analyze", AgentContextPolicy.REQUIREMENT),
        ("requirement_parse", AgentContextPolicy.REQUIREMENT),
        ("confirmed_execution_load", AgentContextPolicy.EXECUTION),
        ("sql_plan", AgentContextPolicy.EXECUTION),
        ("sql_generate", AgentContextPolicy.EXECUTION),
        ("data_detect", AgentContextPolicy.EXECUTION),
        ("report_plan", AgentContextPolicy.REPORT),
        ("report_assemble", AgentContextPolicy.REPORT),
    ])
    def test_prefix_mapping(self, agent_name, expected):
        assert ContextPolicyResolver().resolve(agent_name) == expected

    def test_unknown_falls_back_to_requirement(self):
        assert ContextPolicyResolver().resolve("xyzzy") == AgentContextPolicy.REQUIREMENT
        assert ContextPolicyResolver().resolve("") == AgentContextPolicy.REQUIREMENT


# --- assembler.py -----------------------------------------------------------


class TestContextAssembler:
    def test_assemble_returns_bundle_with_passthrough_conversation(self):
        # 关键：conversation_context 字段透传输入；不含 format_context_block 包裹
        asm = ContextAssembler()
        bundle = asm.assemble(
            conversation_context="hello",
            recall_items=[RecallItem(raw_text="pref", source="legacy_memory_manager")],
            agent_policy=AgentContextPolicy.REQUIREMENT,
        )
        assert bundle["conversation_context"] == "hello"
        assert "<对话上下文" not in bundle["conversation_context"]
        assert bundle["agent_policy"] == "requirement"
        assert bundle["schema_version"] == "v2"

    def test_assemble_preserves_recall_items(self):
        asm = ContextAssembler()
        items = [
            RecallItem(raw_text="q1", source="legacy_memory_manager"),
            RecallItem(raw_text="p1", source="legacy_memory_manager"),
        ]
        bundle = asm.assemble(
            conversation_context="ctx", recall_items=items,
            agent_policy=AgentContextPolicy.EXECUTION,
        )
        assert bundle["recall_items"] == items

    def test_assemble_filters_empty_recall_items(self):
        asm = ContextAssembler()
        items = [
            RecallItem(raw_text="", source="legacy_memory_manager"),
            RecallItem(raw_text="keep", source="legacy_memory_manager"),
        ]
        bundle = asm.assemble(
            conversation_context="ctx", recall_items=items,
            agent_policy=AgentContextPolicy.REPORT,
        )
        # Filter step：drop 空 raw_text
        kept = [i for i in bundle["recall_items"] if i["raw_text"]]
        assert len(kept) == 1
        assert kept[0]["raw_text"] == "keep"

    def test_assemble_builds_assembled_context_in_priority_order(self):
        # memory-architecture §七 固定序：recall（含 Preference/Query）在 conversation 之前
        asm = ContextAssembler()
        bundle = asm.assemble(
            conversation_context="CONV",
            recall_items=[RecallItem(raw_text="RECALL", source="legacy_memory_manager")],
            agent_policy=AgentContextPolicy.REQUIREMENT,
        )
        assert "RECALL" in bundle["assembled_context"]
        assert "CONV" in bundle["assembled_context"]
        assert bundle["assembled_context"].index("RECALL") < bundle["assembled_context"].index("CONV")


# --- runtime.py：5 步编排钉子 -----------------------------------------------


class TestContextRuntimeFiveStepOrchestration:
    @pytest.mark.asyncio
    async def test_build_invokes_full_pipeline_with_legacy_fallback(self):
        # mock 5 步涉及的全部外部依赖
        captured: dict = {"recall_calls": []}

        async def fake_prepare_ctx(session_id, user_id):
            captured["conversation"] = (session_id, user_id)
            return "CTX_FROM_ENGINE"

        async def fake_recall_structured(self, query, user_id, *, top_k_queries=2,
                                         top_k_preferences=3):
            # P4b F3：Runtime 经 app.memory.{semantic,query}.recall_structured 调用，
            # 每个 view 内部委托 MemoryManager.recall_structured 一次。
            # LegacyFallbackPolicy 全开 → 调 semantic + query 两个 view → fake 被调两次。
            captured["recall_calls"].append(
                (query, user_id, top_k_queries, top_k_preferences)
            )
            return [{
                "raw_text": "结构化召回", "kind": "query",
                "source": "memory_query", "score": 0.9, "ref_id": 3,
            }]

        with patch(
            "app.context.runtime.prepare_conversation_context",
            new=fake_prepare_ctx,
        ), patch(
            "app.infra.memory.memory_manager.MemoryManager.recall_structured",
            new=fake_recall_structured,
        ):
            runtime = ContextRuntime()
            bundle = await runtime.build(
                session_id="s-1", user_id=42,
                query="2024 销售", agent="requirement_analyze",
            )

        # Step 1：agent 解析
        assert bundle["agent_policy"] == "requirement"
        # Step 2：fallback 全开 → conversation=True → 调用 _prepare
        assert captured["conversation"] == ("s-1", 42)
        assert bundle["conversation_context"] == "CTX_FROM_ENGINE"
        # Step 3-4：fallback semantic+query=True → 调两个 view 各一次：
        #   - semantic view 内部传 top_k_queries=0 / top_k_preferences=3
        #   - query view 内部传 top_k_queries=2 / top_k_preferences=0
        assert len(captured["recall_calls"]) == 2
        sem_call = next(c for c in captured["recall_calls"] if c[2] == 0)
        qry_call = next(c for c in captured["recall_calls"] if c[3] == 0)
        assert sem_call == ("2024 销售", "42", 0, 3)
        assert qry_call == ("2024 销售", "42", 2, 0)
        # query view 过滤 source=="memory_query" → bundle 含 query 类 item
        assert bundle["recall_items"][0]["kind"] == "query"
        assert bundle["recall_items"][0]["source"] == "memory_query"
        assert bundle["recall_items"][0]["ref_id"] == 3
        # Step 5：assemble → assembled_context 含 结构化召回 + CTX
        assert "结构化召回" in bundle["assembled_context"]
        assert "CTX_FROM_ENGINE" in bundle["assembled_context"]
        # schema_version 字段
        assert bundle["schema_version"] == "v2"

    @pytest.mark.asyncio
    async def test_build_skips_recall_when_decision_says_no(self):
        class NoRecallPolicy:
            def decide(self, *, query, agent_policy, session_state):
                return RecallDecision(conversation=True, semantic=False, query=False)

        async def fake_prepare_ctx(session_id, user_id):
            return "CTX"

        recall_called = {"hit": False}

        async def fake_recall_structured(self, query, user_id, *, top_k_queries=2,
                                         top_k_preferences=3):
            recall_called["hit"] = True
            return []

        with patch(
            "app.context.runtime.prepare_conversation_context",
            new=fake_prepare_ctx,
        ), patch(
            "app.infra.memory.memory_manager.MemoryManager.recall_structured",
            new=fake_recall_structured,
        ):
            runtime = ContextRuntime(policy=NoRecallPolicy())
            bundle = await runtime.build(
                session_id="s", user_id=1, query="q", agent="requirement_x",
            )

        assert recall_called["hit"] is False  # decision.semantic+query 全 False → 不调 recall
        assert bundle["recall_items"] == []

    @pytest.mark.asyncio
    async def test_build_empty_recall_yields_empty_items(self):
        """recall_structured() 返回空 list（无召回）→ RecallItem 列表为空。"""
        async def fake_prepare_ctx(session_id, user_id):
            return "CTX"

        async def fake_recall_structured(self, query, user_id, *, top_k_queries=2,
                                         top_k_preferences=3):
            return []  # 无 active 召回

        with patch(
            "app.context.runtime.prepare_conversation_context",
            new=fake_prepare_ctx,
        ), patch(
            "app.infra.memory.memory_manager.MemoryManager.recall_structured",
            new=fake_recall_structured,
        ):
            runtime = ContextRuntime()
            bundle = await runtime.build(
                session_id="s", user_id=1, query="q", agent="sql_x",
            )
        assert bundle["recall_items"] == []
        assert bundle["agent_policy"] == "execution"
