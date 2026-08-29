"""P4c Task 1：4 graph caller 必须真正接入 ContextRuntime。

钉子：
- 2 个 graph 入口节点（requirement_analysis_graph._requirement_parse +
  confirmed_execution_graph._confirmed_sql_agent）必须调用 ContextRuntime.build()
- 4 个 prompt 注入点（requirement_parser.parse_requirement /
  requirement_parser._call_llm_for_parse / sql_graph._plan /
  sql_graph._generate_sql）必须引用 `assembled_context`
- facade `build_session_context` 兼容路径保留（仅 DeprecationWarning）
- `app.context.context_runtime` 单例存在
"""
from __future__ import annotations

import importlib
import inspect
import warnings

import pytest


@pytest.mark.parametrize("module_path,func_name", [
    ("app.agent.requirement_analysis_graph", "_requirement_parse"),
    ("app.agent.confirmed_execution_graph", "_confirmed_sql_agent"),
])
def test_graph_entry_node_calls_context_runtime_build(module_path, func_name):
    """入口节点函数源含 `context_runtime.build(` 或 `ContextRuntime().build(` 调用。"""
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    src = inspect.getsource(func)
    assert ("context_runtime.build(" in src) or (
        "ContextRuntime(" in src and ".build(" in src
    ), (
        f"{module_path}.{func_name} 仍未调用 context_runtime.build() / ContextRuntime().build()"
    )


@pytest.mark.parametrize("module_path,func_name", [
    ("app.agent.requirement_parser", "parse_requirement"),
    ("app.agent.requirement_parser", "_call_llm_for_parse"),
    ("app.agent.sql_graph", "_plan"),
    ("app.agent.sql_graph", "_generate_sql"),
])
def test_prompt_injectors_use_assembled_context(module_path, func_name):
    """4 个 prompt 注入点必须从 ContextBundle.assembled_context 注入。"""
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    src = inspect.getsource(func)
    assert "assembled_context" in src, (
        f"{module_path}.{func_name} 未引用 assembled_context"
    )


def test_facade_build_session_context_still_callable():
    """facade 兼容路径保留：旧 import 路径仍工作（仅 DeprecationWarning）。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from app.context import build_session_context
        assert callable(build_session_context)


def test_module_level_context_runtime_singleton():
    """app.context.context_runtime 模块单例存在。"""
    from app.context.runtime import context_runtime
    assert context_runtime is not None
