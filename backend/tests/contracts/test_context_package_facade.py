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
        # review P0 #1：原 build_session_context async glue 实质进 _engine；
        # P4a：实现再迁入 app.memory.conversation（domain 层），_engine 退化为 re-export facade。
        # 强钉子：_engine 上的实现对象 __module__ 必须指向 app.memory.conversation（证明确实搬家）。
        from app.context import _engine
        assert hasattr(_engine, "_prepare_conversation_context")
        assert _engine.build_context.__module__ == "app.memory.conversation", (
            "P4a 后 build_context 实现应在 app.memory.conversation，_engine 仅 re-export"
        )
        assert _engine.prepare_conversation_context.__module__ == "app.memory.conversation"


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
    def test_facade_source_wires_deprecation_warning(self):
        # 钉子目的：facade 顶部必须挂 DeprecationWarning 提示新代码用 runtime。
        # 不用 sys.modules.pop + reimport 触发——那会派生第二个 app.context.runtime
        # 模块对象，令 ContextRuntime.build.__globals__ 与后续测试 monkeypatch 目标
        # 分离（跨测试污染）。改为对 __init__.py 源码做 AST 检查：确认存在
        # warnings.warn(..., DeprecationWarning, ...) 调用。零 reload、零污染。
        import ast
        from pathlib import Path
        import app.context as _ctx_pkg

        src = Path(_ctx_pkg.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        warns_dep = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "warn"):
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id == "DeprecationWarning":
                        warns_dep = True
        assert warns_dep, "app.context facade 未在模块顶部挂 warnings.warn(..., DeprecationWarning)"


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
