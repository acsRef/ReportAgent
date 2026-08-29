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
import re
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


# P4c post-review F2 第二轮: 防 caller 假装传 fake remaining_token_budget.
# 当前项目无 unified input context window / prompt budget accounting 来源
# (CLAUDE.md §8 P5/P6 Unified LLM Migration 收敛点). 故 caller 真不传
# remaining_token_budget (走 None → configured-only 4000 tokens 路径).
# 此钉防 caller 给 remaining_token_budget 传字面整数 (~=8000/16000/4000)
# 假装 "P4c 完成".

_FAKE_BUDGET_PATTERNS = [
    re.compile(r"remaining_token_budget\s*=\s*\d{3,}"),
    re.compile(r"remaining_token_budget\s*=\s*os\.getenv"),  # 不该用 env 临时填
]


@pytest.mark.parametrize("module_path,func_name", [
    ("app.agent.requirement_analysis_graph", "_requirement_parse"),
    ("app.agent.confirmed_execution_graph", "_confirmed_sql_agent"),
])
def test_graph_caller_does_not_invent_remaining_budget(module_path, func_name):
    """钉 caller 不传 fake 字面量 remaining_token_budget.

    Why: P4c post-review 第二轮指明 — 项目无 unified prompt budget 来源前,
    caller 显式不传 remaining_token_budget 是 honest deferred state. 若未来
    有人想 "fake pass" (传 4000/8000 等字面量), 这钉会立刻拦截. 真要做
    时用 var/state-derived 值, 并人工更新此钉 (附 plan doc).
    """
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    src = inspect.getsource(func)
    for pattern in _FAKE_BUDGET_PATTERNS:
        match = pattern.search(src)
        assert match is None, (
            f"{module_path}.{func_name} 不应给 remaining_token_budget 传 "
            f"字面/凑数预算 ({match.group(0) if match else ''!r}). "
            f"当前应 None (走 configured-only 4000 tokens 路径), 等待 P5/P6 "
            f"unified accounting 收敛后再传真实值."
        )
