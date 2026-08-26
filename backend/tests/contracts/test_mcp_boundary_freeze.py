"""P2 MCP Boundary Freeze - Import 边界钉子（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 5 Step 1）。

规则：
1. backend/app 下 `app.tools.rag_schema` / `app.tools.interface_dict_tools` 的 import
   只允许出现在 tools/ 包内——其他子包（agent/ infra/ llm/ observability/ ...）禁止引用
   业务传输层模块（边界归 tools/ 管）。
2. backend/app 下 `app.tools.mcp_client` / `app.tools.mcp_errors` 同上（boundary 自身
   只允许被 tools/ 内的 dispatcher / shim 引用）。
3. backend/app 任何文件禁止真 `import mcp_server` / `from mcp_server`——防聪明 adapter
   绕道 MCP boundary 直连 RAG 内部 Python 模块。
   豁免：tools/ 包内 StdioServerParameters / env var 默认值的字符串字面量
   （`"mcp_server.server"` 仅作模块名字符串，AST 扫描不命中 import 语句）。
4. backend/app 任何文件禁止硬编码 `D:/PyProject/ragent-py` 路径字面量——env 注入。
   豁免：tools/ 包内 env var fallback 默认值（runtime 可被 RAGENT_MCP_CWD 覆盖）。

离线可跑：纯 AST + 文本扫描，不触 PG / LLM / MCP。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TOOLS_DIR = (APP_DIR / "tools").resolve()
LEGACY_DIR = (APP_DIR / "legacy").resolve()

# 业务传输层模块——boundary 归 tools/ 管，其他子包禁止引用。
FORBIDDEN_TOOL_MODULES: frozenset[str] = frozenset(
    {
        "app.tools.rag_schema",
        "app.tools.interface_dict_tools",
        "app.tools.mcp_client",
        "app.tools.mcp_errors",
    }
)

# 硬编码路径字面量——env 注入；tools/ 包内 fallback 默认值豁免。
FORBIDDEN_PATH_LITERAL = "D:/PyProject/ragent-py"


def _parse(text: str) -> ast.AST:
    # 部分源文件带 UTF-8 BOM（data_graph/report_graph/sdk/db），ast.parse 不收。
    return ast.parse(text.lstrip("﻿"))


def _import_entries(tree: ast.AST) -> list[tuple[str, int]]:
    """返回 [(module_name, lineno), ...]，覆盖 Import + ImportFrom。"""
    out: list[tuple[str, int]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.module:
                out.append((n.module, getattr(n, "lineno", 0)))
        elif isinstance(n, ast.Import):
            for a in n.names:
                out.append((a.name, getattr(n, "lineno", 0)))
    return out


def _iter_app_py() -> list[Path]:
    """扫描 backend/app 下所有 .py（legacy/ 包沿用 P1 freeze 风格豁免）。"""
    return [
        p
        for p in APP_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
        and LEGACY_DIR not in p.resolve().parents
    ]


# ---------------------------------------------------------------------------
# 测试 1：业务传输层模块只能在 tools/ 包内被 import
# ---------------------------------------------------------------------------


def test_outside_tools_cannot_import_rag_or_dict_modules():
    """`app.tools.rag_schema` / `app.tools.interface_dict_tools` 是 schema/字典检索
    的传输层入口；其他子包（agent/ infra/ llm/ observability/ ...）禁止引用——
    否则绕过 dispatcher 直接走 HTTP fallback 或触碰 chunk 解析。

    P2 决策 5（钉子 1）原文：「禁止 `from app.tools.rag_schema import` /
    `import rag_schema` / `interface_dict_tools` 出现在 tools/ 层之外」。
    """
    violations: list[str] = []
    for py in _iter_app_py():
        rel = py.relative_to(APP_DIR.parent)
        try:
            tree = _parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # tools/ 包内自引允许（rag_schema ↔ mcp_client 等）
        if TOOLS_DIR in py.resolve().parents:
            continue
        for module, lineno in _import_entries(tree):
            if module in FORBIDDEN_TOOL_MODULES:
                violations.append(f"{rel}:{lineno} → {module}")
    assert not violations, (
        "业务传输层模块（rag_schema / interface_dict_tools）只能由 tools/ 包引用"
        "（p2-rag-mcp-boundary 决策 5 / 决策 1 边界）:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 测试 2：boundary 自身（mcp_client / mcp_errors）也只能被 tools/ 内引用
# ---------------------------------------------------------------------------


def test_outside_tools_cannot_import_mcp_boundary():
    """`app.tools.mcp_client` / `app.tools.mcp_errors` 是 MCP boundary 自身；
    只允许 tools/ 包内的 dispatcher / shim 引用。其他子包如 agent/ graphs/
    自行 import boundary 会破坏职责划分（boundary 是传输层细节，不该渗透到
    graph 节点或 observability 适配器）。

    P2 决策 5（钉子 1）原文：「`app.tools.mcp_client` / `app.tools.mcp_errors`
    的 import 只允许出现在 tools/ 包内」。
    """
    violations: list[str] = []
    for py in _iter_app_py():
        rel = py.relative_to(APP_DIR.parent)
        try:
            tree = _parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if TOOLS_DIR in py.resolve().parents:
            continue
        for module, lineno in _import_entries(tree):
            # 仅约束 boundary 自身模块（其余 FORBIDDEN_TOOL_MODULES 在测试 1 已约束）
            if module in {"app.tools.mcp_client", "app.tools.mcp_errors"}:
                violations.append(f"{rel}:{lineno} → {module}")
    assert not violations, (
        "MCP boundary 自身（mcp_client / mcp_errors）只能由 tools/ 包内 dispatcher 引用"
        "（p2-rag-mcp-boundary 决策 5）:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 测试 3：禁止 backend/app 真 import mcp_server
# ---------------------------------------------------------------------------


def test_no_mcp_server_import_in_app():
    """禁止 backend/app 任何文件 `import mcp_server` / `from mcp_server`——
    防聪明 adapter 绕道 MCP boundary 直连 RAG 内部 Python 模块（违反宪法
    Forbidden Patterns 第 8 条「不绕过 MCP 直连 RAG 内部机制」）。

    豁免：tools/ 包内的 StdioServerParameters / env var 默认值字符串字面量
    （如 `os.getenv("RAGENT_MCP_MODULE", "mcp_server.server")`），AST 扫描
    import 语句天然不命中字符串字面量。
    """
    violations: list[str] = []
    for py in _iter_app_py():
        rel = py.relative_to(APP_DIR.parent)
        try:
            tree = _parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for module, lineno in _import_entries(tree):
            if module == "mcp_server" or module.startswith("mcp_server."):
                violations.append(f"{rel}:{lineno} → {module}")
    assert not violations, (
        "禁止 backend/app 真 import mcp_server（防绕过 MCP boundary）:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 测试 4：禁止非 tools/ 包硬编码 ragent-py 路径字面量
# ---------------------------------------------------------------------------


def test_no_hardcoded_ragent_py_path_outside_tools():
    """禁止 backend/app 非 tools/ 包硬编码 `D:/PyProject/ragent-py` 路径——env 注入
    （`RAGENT_MCP_CWD`）。tools/ 包内的 env var fallback 默认值豁免（runtime 可被覆盖）。

    P2 决策 5（钉子 1）原文：「全文禁 `D:/PyProject/ragent-py`」（除 mcp_faq_client
    StdioServerParameters 配置项字符串）—— mcp_client.py / mcp_faq_client.py 同属
    tools/ 包内的 env-driven 配置项，统一豁免。
    """
    violations: list[str] = []
    for py in _iter_app_py():
        rel = py.relative_to(APP_DIR.parent)
        if TOOLS_DIR in py.resolve().parents:
            continue
        text = py.read_text(encoding="utf-8")
        if FORBIDDEN_PATH_LITERAL in text:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if FORBIDDEN_PATH_LITERAL in line:
                    violations.append(f"{rel}:{lineno}")
                    break
    assert not violations, (
        "禁止 backend/app 非 tools/ 包硬编码 ragent-py 路径（env 注入；p2-rag-mcp-boundary 决策 5）:\n"
        + "\n".join(violations)
    )