"""P3 Task 3 (γ) graph 入口节点 migrate_checkpoint 注入钉子。

P3 plan §2.4 review #8 决议：不造 SchemaVersioningSaver wrapper；checkpoint
migrate 落地为 graph 入口节点 body 首行 `state = migrate_checkpoint(dict(state))`
（compatibility adapter 一行；不重写 state 字段访问方式）。

本钉子用 AST 解析函数体第一条非-docstring 语句，断言：
- 是 `Assign(targets=[Name('state')], value=Call(func=Name('migrate_checkpoint')))`
"""
from __future__ import annotations

import ast
import importlib
import inspect

import pytest

# 5 个现役 graph 入口节点（按 workflow.set_entry_point 确认）
ENTRY_NODES = [
    ("app.agent.requirement_analysis_graph", "_security_guard"),
    ("app.agent.confirmed_execution_graph", "_security_guard"),
    ("app.agent.sql_graph", "_plan"),
    ("app.agent.data_graph", "_detect_intent"),
    ("app.agent.report_graph", "_plan_analysis"),
]


def _first_meaningful_stmt(fn_obj) -> ast.stmt | None:
    """取函数 body 第一条非 docstring / 非空语句。"""
    src = inspect.getsource(fn_obj)
    # 处理 decorator：inspect.getsource 可能含 decorator；parse 取最后一个
    # FunctionDef（decorator 在 Module body 中是 FunctionDef 带 decorator_list）
    tree = ast.parse(src)
    func_def = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_def = node
            break
    assert func_def is not None, f"未在 {fn_obj.__qualname__} 源码中找到 FunctionDef"
    body = list(func_def.body)
    # 跳 docstring
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body[0] if body else None


@pytest.mark.parametrize("module_path,fn_attr", ENTRY_NODES)
def test_entry_node_first_stmt_is_migrate_checkpoint_assignment(module_path, fn_attr):
    """(γ) 决议：graph 入口节点 body 第一条语句必须是
    `state = migrate_checkpoint(...)`。
    """
    module = importlib.import_module(module_path)
    fn = getattr(module, fn_attr)
    first = _first_meaningful_stmt(fn)
    assert first is not None, f"{module_path}.{fn_attr} 函数体为空"
    assert isinstance(first, ast.Assign), (
        f"{module_path}.{fn_attr} 首句不是赋值；(γ) 注入未实施或位置错。"
        f"实际：{ast.dump(first)[:120]}"
    )
    assert len(first.targets) == 1
    target = first.targets[0]
    assert isinstance(target, ast.Name) and target.id == "state", (
        f"{module_path}.{fn_attr} 首句赋值目标应为 state，实际 {ast.dump(target)}"
    )
    value = first.value
    assert isinstance(value, ast.Call), (
        f"{module_path}.{fn_attr} 首句赋值 value 应为 Call"
    )
    assert isinstance(value.func, ast.Name) and value.func.id == "migrate_checkpoint", (
        f"{module_path}.{fn_attr} 首句应调 migrate_checkpoint，"
        f"实际 {ast.dump(value.func)[:80]}"
    )


@pytest.mark.parametrize("module_path,fn_attr", ENTRY_NODES)
def test_module_imports_migrate_checkpoint(module_path, fn_attr):
    """入口节点所在 module 必须 import migrate_checkpoint（钉子）。"""
    module = importlib.import_module(module_path)
    assert hasattr(module, "migrate_checkpoint"), (
        f"{module_path} 未 import migrate_checkpoint"
    )
