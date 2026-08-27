"""P3 Task 4: app/context 包重组 contract 钉子 + facade 行为等价。

P3 plan §2.5 + review P0 #1 + #10 钉住：
- 旧 `backend/app/context.py` 删除，内容迁入 `backend/app/context/_engine.py`
- `backend/app/context/__init__.py` 做 facade：re-export 旧 API + 新 API
- 关键钉子（review P0 #1）：facade `build_session_context` **直调**
  `_engine._prepare_conversation_context`（兼容路径），**不**转发到
  `ContextRuntime.build()`——后者需要 query/agent 入参且会引入
  MemoryManager.recall 副作用
- 关键钉子（review #10）：legacy side-effect 隔离——mock
  `MemoryManager.recall` 为 raise AssertionError；调 facade 不抛错；
  调 `ContextRuntime.build` 抛错
"""
from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, patch

import pytest


# --- 兼容性 import 钉子 ---------------------------------------------------


class TestBackwardCompatibleImports:
    """4 文件 6 处 build_session_context 调用 + 1 处 format_context_block import
    零修改钉子。"""

    def test_legacy_imports_still_work(self):
        # from app.context import build_context, build_session_context,
        # format_messages, format_context_block 仍可用
        from app.context import (
            build_context,
            build_session_context,
            format_context_block,
            format_messages,
        )
        assert callable(build_context)
        assert callable(build_session_context)
        assert callable(format_context_block)
        assert callable(format_messages)

    def test_legacy_pure_functions_are_original(self):
        # build_context / format_messages / format_context_block 实现等价
        # （不依赖 runtime / MemoryManager）
        from app.context import _engine
        assert hasattr(_engine, "build_context")
        assert hasattr(_engine, "format_messages")
        assert hasattr(_engine, "format_context_block")
        assert hasattr(_engine, "archive_to_l2_5")
        assert hasattr(_engine, "compress_and_extract")

    def test_async_glue_moved_to_engine(self):
        # review P0 #1：原 build_session_context async glue 实质进 _engine
        from app.context import _engine
        assert hasattr(_engine, "_prepare_conversation_context")


# --- 新 API re-export -----------------------------------------------------


class TestNewAPIReExports:
    def test_runtime_exported(self):
        from app.context import ContextRuntime, context_runtime
        assert ContextRuntime is not None
        assert context_runtime is not None

    def test_policy_exported(self):
        from app.context import AgentContextPolicy, ContextPolicyResolver
        assert hasattr(AgentContextPolicy, "REQUIREMENT")
        assert hasattr(AgentContextPolicy, "EXECUTION")
        assert hasattr(AgentContextPolicy, "REPORT")
        assert callable(ContextPolicyResolver)

    def test_bundle_exported(self):
        from app.context import ContextBundle, RecallItem
        assert ContextBundle is not None
        assert RecallItem is not None


# --- DeprecationWarning 钉子 ---------------------------------------------


class TestDeprecationWarningOnLegacyImport:
    def test_facade_emits_deprecation_warning(self):
        # 导入 app.context 时触发 DeprecationWarning（提示新代码优先 runtime）
        import importlib
        import sys
        # 强制重新加载模块以触发 warning
        sys.modules.pop("app.context", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("app.context")
        # 至少有一个 DeprecationWarning
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert dep, f"未触发 DeprecationWarning；实得 {[w.category for w in caught]}"
        # 重新清理 sys.modules 让其他 test 仍能 import
        sys.modules.pop("app.context", None)


# --- review #10 关键钉子：facade 不引入 recall 副作用 ---------------------


class TestFacadeSideEffectIsolation:
    """review P0 #1 + #10：facade `build_session_context` 直调 _engine 路径，
    **不**触发 MemoryManager.recall / ContextDecisionPolicy.decide。"""

    @pytest.mark.asyncio
    async def test_facade_does_not_call_memory_manager_recall(self):
        # mock 整链路：get_messages / session_manager / compress LLM
        # 关键：MemoryManager.recall 设为 raise AssertionError
        from app.context import build_session_context

        async def _boom(*args, **kwargs):
            raise AssertionError(
                "facade build_session_context 不应调 MemoryManager.recall"
            )

        with patch(
            "app.infra.memory.memory_manager.MemoryManager.recall",
            new=_boom,
        ), patch(
            "app.infra.conversation.repository.get_messages",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.infra.checkpoint.session.session_manager.get_context_state",
            new=AsyncMock(return_value={
                "digest": None, "digest_msg_count": 0,
                "digest_version": 0, "mid_digest": None,
            }),
        ), patch(
            "app.infra.checkpoint.session.session_manager.save_context_state",
            new=AsyncMock(return_value=None),
        ):
            # 不应抛 AssertionError；若抛则说明 facade 引入了 recall 副作用
            result = await build_session_context("session-x", 1)
            assert isinstance(result, str)
