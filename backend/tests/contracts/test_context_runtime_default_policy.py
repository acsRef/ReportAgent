"""P4c post-review F1 contract: ContextRuntime 默认 policy 必须 SelectiveRecallPolicy.

根因（review REQUEST CHANGES P1-1）: 之前 default = LegacyFallbackPolicy() 会让
所有 4 graph caller (ContextRuntime().build(...)) 都走全开召回, 与 P4c plan
"主图真实触发 selective" 目标直接冲突.

防御: 防止后续重构悄悄改回 LegacyFallbackPolicy. 修改前必须 discussion; 改回
LegacyFallbackPolicy 应仅作为显式兼容路径 (ContextRuntime(policy=LegacyFallbackPolicy())).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.context.decision import LegacyFallbackPolicy, SelectiveRecallPolicy


class TestContextRuntimeDefaultPolicy:
    def test_default_policy_class_is_selective_recall(self):
        """ContextRuntime() 默认 policy 类必须为 SelectiveRecallPolicy (plan 目标)."""
        from app.context.runtime import ContextRuntime
        runtime = ContextRuntime()
        assert isinstance(runtime._policy, SelectiveRecallPolicy), (
            f"默认 policy 必须是 SelectiveRecallPolicy, "
            f"got {type(runtime._policy).__name__}. "
            f"如需 fallback, 显式传 ContextRuntime(policy=LegacyFallbackPolicy())."
        )

    def test_explicit_legacy_fallback_works(self):
        """LegacyFallbackPolicy 仍可作为显式兼容策略使用."""
        from app.context.runtime import ContextRuntime
        runtime = ContextRuntime(policy=LegacyFallbackPolicy())
        assert isinstance(runtime._policy, LegacyFallbackPolicy)

    def test_module_level_singleton_uses_selective(self):
        """app.context.runtime.context_runtime 单例默认也是 SelectiveRecallPolicy."""
        from app.context.runtime import context_runtime
        assert isinstance(context_runtime._policy, SelectiveRecallPolicy), (
            "module-level 单例必须跟随默认: SelectiveRecallPolicy"
        )


class TestContextRuntimeBuildUsesSelectivePolicy:
    """P4c review P1-1 真路径验证: build() 在 default config 下应走 SelectiveRecallPolicy
    行为 (非 Legacy 全开). 用一个 fake decision policy 验证 build() 真调 _policy.decide()."""

    def test_build_invokes_assigned_policy_not_some_global(self):
        from app.context.runtime import ContextRuntime
        from app.context.decision import ContextDecisionPolicy, RecallDecision

        called = {"count": 0}

        class RecordingPolicy(ContextDecisionPolicy):
            def decide(self, *, query, agent_policy, session_state):
                called["count"] += 1
                return RecallDecision(
                    conversation=False, semantic=False, query=False,
                    top_k_queries=0, top_k_preferences=0, rationale="rec",
                )

        runtime = ContextRuntime(policy=RecordingPolicy())
        # patch memory recall 为 noop + prepare_conversation_context 为 stub (DB-free)
        with patch("app.context.runtime.prepare_conversation_context",
                   return_value=""):
            with patch("app.memory.semantic.recall_structured",
                       return_value=[]):
                with patch("app.memory.query.recall_structured",
                           return_value=[]):
                    import asyncio
                    bundle = asyncio.run(runtime.build(
                        session_id="s", user_id=1,
                        query="你好", agent="requirement_analyze",
                    ))

        assert called["count"] == 1, "build() 必须调用 _policy.decide()"
        # selective 对 "你好" 走 chitchat → all False → assembled 空
        assert bundle["assembled_context"] == "", (
            f"selective 真触发的标志: '你好' chitchat 应让 assembled_context 为空, "
            f"got {bundle['assembled_context']!r}"
        )
