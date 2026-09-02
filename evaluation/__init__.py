"""ReportAgent evaluation harness —— P14 升级。

模块级 import 9 子包（side-effect：register_dim 把 assert_<dim> 注册到 DIM_REGISTRY），
保证任何 evaluation.* 入口（CLI / pytest / 任意子模块）都能拿到完整 DIM_REGISTRY，
不依赖任何 test file 提前 import。

入口优先级：
- import evaluation        → evaluation/__init__.py 触发 9 子包 import
- from evaluation.checker  → DIM_REGISTRY 已满（含 9 entries）
- from evaluation.runner   → DIM_REGISTRY 已满
- python -m evaluation.runner → DIM_REGISTRY 已满
"""
# P14 P2 闭环：模块级显式 import 9 子包，让 DIM_REGISTRY 启动期间注册完整。
# 替代之前 runner.py 顶部 import（不够稳健：未 import runner 的路径仍空）
from evaluation import (  # noqa: E402, F401  —— register_dim side-effect
    e2e,
    frontend,
    memory,
    repair,
    report,
    requirement,
    retrieval,
    sql,
    tool_selection,
)
