"""P2 MCP Boundary Freeze - Tool Allowlist 钉子（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 5 钉子 2）。

规则：
1. registry 注册面 == 12 个白名单工具（5 data + 2 sql + 5 report）——新工具加入必须
   同步更新白名单（迫使开发者显式确认业务语义）。
2. 所有注册工具 metadata.source ∈ {"local", "mcp"}——source 字段是 Agent Contract 一部分，
   非法值会让 dispatcher / 审计逻辑误判。
3. schema 三工具（search_tables / get_table_ddl / list_tables）metadata.source == "mcp"——
   它们底层走 MCP dispatcher（ragent-py search_dictionary 通道）。
4. 禁入 RAG 内部机制相关工具名：embedding / vector_search / rerank / chunk /
   query_pgvector / ingest / upsert / list_docs / kb_manage 子串——防有人把 RAG
   内部 API 包装成 ReportAgent 工具（违反 Forbidden Patterns 第 8 条「不绕过 MCP
   直连 RAG 内部机制」）。
5. 每个工具 description 必含「用于：」边界行——五要素之一，未交代使用场景会让模型乱调。

离线可跑：registry 内存枚举，不触 PG / LLM / MCP。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.tools import register_all_tools
from app.tools.registry import registry


EXPECTED_TOOLS: frozenset[str] = frozenset(
    {
        # 数据 Agent 工具 —— 表结构发现
        "search_tables",
        "get_table_ddl",
        "list_tables",
        "search_interface_dictionary",
        "search_faq",
        # SQL Agent 工具 —— 校验与执行
        "validate_sql",
        "execute_sql",
        # 报告 Agent 工具 —— 可视化与洞察
        "chart_advisor",
        "insight_analyst",
        "trend_analysis",
        "group_compare",
        "detect_anomaly",
    }
)

# RAG 内部机制关键词——子串匹配（禁入决策 2：ragent-py 内部 API 不外露为工具）
FORBIDDEN_TOOL_NAME_TOKENS: tuple[str, ...] = (
    "embedding",
    "vector_search",
    "rerank",
    "chunk",
    "query_pgvector",
    "ingest",
    "upsert",
    "list_docs",
    "kb_manage",
)

# 必须 source="mcp" 的 schema 检索工具（dispatcher 底层走 MCP）
MCP_SOURCED_TOOLS: frozenset[str] = frozenset(
    {"search_tables", "get_table_ddl", "list_tables"}
)

ALLOWED_SOURCE_VALUES: frozenset[str] = frozenset({"local", "mcp"})


@pytest.fixture
def registered() -> None:
    """确保 registry 已填充（register_all_tools 幂等，多次调用安全）。"""
    register_all_tools()


# ---------------------------------------------------------------------------
# 钉子 1：注册面 == 白名单（双向断言）
# ---------------------------------------------------------------------------


def test_registry_matches_expected_allowlist(registered):
    """registry 注册工具必须恰好是 12 个白名单成员——未授权新工具即红，
    迫使开发者更新白名单时显式确认业务语义与边界归属（plan 决策 2）。"""
    actual = set(registry.all_tools().keys())
    extra = actual - EXPECTED_TOOLS
    missing = EXPECTED_TOOLS - actual
    assert not extra, (
        f"registry 注册了非白名单工具（新增须更新白名单）:\n"
        f"  实际: {sorted(actual)}\n"
        f"  多出: {sorted(extra)}"
    )
    assert not missing, (
        f"白名单声明的工具不在 registry（已删除？须同步白名单）:\n"
        f"  缺失: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# 钉子 2：source 字段全局约束 + schema 三工具必须 "mcp"
# ---------------------------------------------------------------------------


def test_source_field_constrained_to_local_or_mcp(registered):
    """所有注册工具 metadata.source ∈ {"local", "mcp"}——非法值会让 dispatcher
    / 审计 / 限流逻辑误判。ToolMetadata 默认 source="local"，故空值也算违规。"""
    bad: list[str] = []
    for name, meta in registry.all_tools().items():
        if meta.source not in ALLOWED_SOURCE_VALUES:
            bad.append(f"{name}: source={meta.source!r}")
    assert not bad, (
        "tool metadata.source 必须 ∈ {'local', 'mcp'}:\n" + "\n".join(bad)
    )


def test_schema_three_tools_marked_as_mcp_source(registered):
    """schema 三工具（search_tables / get_table_ddl / list_tables）底层走 MCP
    dispatcher（ragent-py search_dictionary 检索通道），故 metadata.source 必须
    == "mcp"——plan 决策 2 + 决策 5 钉子 2 原文：「schema 三工具 source=='mcp'」。

    source 标记让审计/限流/trace 知道这是 MCP 通道调用，便于与 fallback 路径
    区分失败语义。"""
    bad: list[str] = []
    for name in MCP_SOURCED_TOOLS:
        meta = registry.get_metadata(name)
        assert meta is not None, f"{name} 未注册"
        if meta.source != "mcp":
            bad.append(f"{name}: source={meta.source!r}（期望 'mcp'）")
    assert not bad, (
        "schema 三工具必须 source='mcp'（底层走 MCP dispatcher，plan 决策 5 钉子 2）:\n"
        + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# 钉子 3：禁入 RAG 内部机制工具名
# ---------------------------------------------------------------------------


def test_no_rag_internal_mechanism_tool_names(registered):
    """禁入 RAG 内部机制相关工具名——embedding / vector_search / rerank / chunk /
    query_pgvector / ingest / upsert / list_docs / kb_manage 子串即红。

    防有人把 RAG 内部 API（ragent-py 的 embed / rerank / chunk / ingest /
    upsert_doc / list_docs 等）包装成 ReportAgent 工具绕过 MCP boundary
    （违反 Forbidden Patterns 第 8 条「不绕过 MCP 直连 RAG 内部机制」；
    plan 决策 2「Agent 永远看不到 RAG 内部机制」）。
    """
    bad: list[str] = []
    for name in registry.all_tools().keys():
        name_lower = name.lower()
        for token in FORBIDDEN_TOOL_NAME_TOKENS:
            if token in name_lower:
                bad.append(f"{name} 命中禁入 token: {token!r}")
    assert not bad, (
        "禁入 RAG 内部机制工具名（plan 决策 2）：\n" + "\n".join(bad)
    )


# ---------------------------------------------------------------------------
# 钉子 4：每个工具 description 必含「用于：」边界行
# ---------------------------------------------------------------------------


def test_each_tool_description_has_when_to_use(registered):
    """每个工具 description 必含「用于：」边界行——五要素之一（用途 + 输入 + 输出 +
    适用 + 不要用来），未交代使用场景会让 LLM 乱调。

    沿用 test_tool_descriptions 风格：逐工具断言 description 关键边界行存在，
    而非仅断言首行长度——「用于：」直接告诉 LLM 何时该用这个工具。"""
    missing: list[str] = []
    for name, meta in registry.all_tools().items():
        if "用于" not in meta.description:
            missing.append(f"{name}: description 缺「用于：」边界行")
    assert not missing, (
        "tool description 缺「用于：」边界行（plan 决策 2 / test_tool_descriptions 风格）:\n"
        + "\n".join(missing)
    )