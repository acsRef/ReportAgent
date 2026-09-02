"""9 子包目录布局测试。

P14 边界：本文件还负责在 pytest 启动阶段显式 import 全部 9 子包，让
DIM_REGISTRY 在初始测试套件运行时注册完整 9 项（frontend/e2e 没 tests 目录，
若不显式 import，DIM_REGISTRY 会少这两项，test_dispatcher 的 registry 检查会闪 RED）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

# 显式 import 全部 9 dim —— 触发子包 __init__.py 的 register_dim 注册
# 顺序稳定：先 harness 子模块、再 __init__ 二次导入可保证幂等（DIM_REGISTRY 去重）
from evaluation import (
    e2e,  # noqa: F401
    frontend,  # noqa: F401
    memory,  # noqa: F401
    repair,  # noqa: F401
    report,  # noqa: F401
    requirement,  # noqa: F401
    retrieval,  # noqa: F401
    sql,  # noqa: F401
    tool_selection,  # noqa: F401
)

EVAL_ROOT = Path(__file__).resolve().parent.parent  # evaluation/tests -> evaluation

EXPECTED_DIMS = [
    "requirement", "memory", "retrieval",
    "tool_selection", "sql", "repair", "report",
    "frontend", "e2e",
]

NON_NOOP_DIMS = EXPECTED_DIMS[:-2]  # 7 个非占位子包


@pytest.mark.parametrize("dim", EXPECTED_DIMS)
def test_each_dim_has_init_harness(dim):
    pkg = EVAL_ROOT / dim
    assert (pkg / "__init__.py").exists(), f"{dim}/__init__.py 缺失"
    assert (pkg / "harness.py").exists(), f"{dim}/harness.py 缺失"


@pytest.mark.parametrize("dim", NON_NOOP_DIMS)
def test_non_noop_dims_have_tests(dim):
    """requirement/memory/retrieval/tool_selection/sql/repair/report 7 个子包需 test_harness.py。

    文件名必须 unique-per-dim（pytest 把同名 test_*.py 撞名时只能保留一个），
    实际命名：test_harness_<dim>.py。
    """
    tests = EVAL_ROOT / dim / "tests" / f"test_harness_{dim}.py"
    assert tests.exists(), f"{dim}/tests/test_harness_{dim}.py 缺失"
