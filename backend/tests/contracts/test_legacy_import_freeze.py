"""P1 Legacy Import Freeze（docs/plans/2026-08-26-p1-architecture-freeze.md 决策 2）。

规则：
1. backend/app 与 backend/tests 下，LEGACY BRIDGE 区之外禁止 import app.legacy*；
2. main.py 必须恰好一对 LEGACY BRIDGE BEGIN / END 标记，区间内只允许 import 语句，
   且 import 集合等于快照（禁止悄悄扩容）。
离线可跑：纯 AST/文本扫描，不触 PG / LLM。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TESTS_DIR = Path(__file__).resolve().parents[1]
MAIN_PY = APP_DIR / "main.py"

BRIDGE_BEGIN = "LEGACY BRIDGE BEGIN"
BRIDGE_END = "LEGACY BRIDGE END"
# main.py 桥接区允许的唯一 import 快照（db.py 已摘除，仅此一条；
# 确需新增时先改快照并过评审）。
ALLOWED_BRIDGE_IMPORTS: frozenset[str] = frozenset({"app.legacy.agents.parent_graph"})


def _bridge_span(lines: list[str]) -> tuple[int, int]:
    """返回 (begin_idx, end_idx)：BEGIN 与 END 标记行的下标。
    缺失、不成对或顺序错误直接断言失败——区块是显式标记界定的，
    不做任何「注释到第一条非注释行为止」式的文本推断。"""
    begins = [i for i, ln in enumerate(lines) if BRIDGE_BEGIN in ln]
    ends = [i for i, ln in enumerate(lines) if BRIDGE_END in ln]
    assert len(begins) == 1 and len(ends) == 1 and begins[0] < ends[0], (
        f"main.py 必须恰好有一对 {BRIDGE_BEGIN}/{BRIDGE_END} 标记且顺序正确"
    )
    return begins[0], ends[0]


def _is_legacy_import(node: ast.stmt) -> bool:
    if isinstance(node, ast.ImportFrom):
        return node.module is not None and (
            node.module == "app.legacy" or node.module.startswith("app.legacy.")
        )
    if isinstance(node, ast.Import):
        return any(
            a.name == "app.legacy" or a.name.startswith("app.legacy.")
            for a in node.names
        )
    return False


def _import_nodes(tree: ast.AST) -> list[ast.stmt]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def _parse(text: str) -> ast.AST:
    # 部分源文件带 UTF-8 BOM（data_graph/report_graph/sdk/db），ast.parse 不收。
    return ast.parse(text.lstrip("﻿"))


def test_no_legacy_imports_outside_bridge():
    violations: list[str] = []
    legacy_pkg = (APP_DIR / "legacy").resolve()

    def iter_py(root: Path):
        for p in root.rglob("*.py"):
            if "__pycache__" not in p.parts and legacy_pkg not in p.resolve().parents:
                yield p

    # 1) main.py：剔除桥接区行后再全文件扫描
    main_lines = MAIN_PY.read_text(encoding="utf-8").splitlines()
    begin, end = _bridge_span(main_lines)
    outside_main = "\n".join(main_lines[:begin]) + "\n" + "\n".join(main_lines[end + 1:])
    for n in _import_nodes(_parse(outside_main)):
        if _is_legacy_import(n):
            violations.append(f"app/main.py: line {getattr(n, 'lineno', '?')}")

    # 2) 其余 app/ + tests/：整文件扫描（legacy 包自身互引已豁免）
    for root in (APP_DIR, TESTS_DIR):
        for py in iter_py(root):
            if py.resolve() == MAIN_PY.resolve():
                continue
            tree = _parse(py.read_text(encoding="utf-8"))
            rel = py.relative_to(APP_DIR.parent)
            for n in _import_nodes(tree):
                if _is_legacy_import(n):
                    violations.append(f"{rel}: line {getattr(n, 'lineno', '?')}")

    assert not violations, (
        "新代码禁止 import legacy（P1 冻结，docs/plans/2026-08-26-p1-architecture-freeze.md 决策 2）:\n"
        + "\n".join(violations)
    )


def test_bridge_imports_frozen():
    lines = MAIN_PY.read_text(encoding="utf-8").splitlines()
    begin, end = _bridge_span(lines)
    inner = _parse("\n".join(lines[begin + 1 : end]))
    non_import = [n for n in inner.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not non_import, (
        f"桥接区内只允许 import 语句，发现: {[type(n).__name__ for n in non_import]}"
    )
    normalized: set[str] = set()
    for n in inner.body:
        if isinstance(n, ast.ImportFrom) and n.module:
            normalized.add(n.module)
        elif isinstance(n, ast.Import):
            normalized.update(a.name for a in n.names)
    assert normalized == set(ALLOWED_BRIDGE_IMPORTS), (
        f"LEGACY BRIDGE 快照漂移: 现为 {sorted(normalized)}, "
        f"允许 {sorted(ALLOWED_BRIDGE_IMPORTS)}。禁止扩大桥接区——"
        "如确需新增，先改本测试快照并过评审。"
    )
