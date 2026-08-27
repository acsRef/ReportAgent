"""P2 MCP Boundary Freeze - Tool Allowlist 钉子（docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 5 钉子 2）。

规则：
1. registry 注册面 == 12 个白名单工具（5 data + 2 sql + 5 report）——新工具加入必须
   同步更新白名单（迫使开发者显式确认业务语义）。
2. 所有注册工具 metadata.source ∈ {"local", "mcp"}——source 语义（review 第 1 轮决议）：
   **「该 Tool 请求满足时的实际正路 runtime 通道」**，不是「capability 的上游来源」。
   非法值会让 dispatcher / 审计逻辑误判。
3. MCP-first dispatcher 工具（search_tables / get_table_ddl）metadata.source == "mcp"——
   它们底层走 MCP dispatcher（ragent-py search_dictionary 通道）；反向钉：list_tables
   无 MCP 等价工具，正路是 _list_dict_docs HTTP 直连，source 必须 == "local"——
   metadata 撒谎会让 trace/审计看到「source=mcp 但无 MCP 调用」。
   （search_interface_dictionary / search_faq 同为 MCP-first，但 source 标注策略
   待 Task 4 mcp-contract.md 定夺，本钉子暂不约束。）
4. 禁入 RAG 内部机制相关工具名：embedding / vector_search / rerank / chunk /
   query_pgvector / ingest / upsert / list_docs / kb_manage 子串——防有人把 RAG
   内部 API 包装成 ReportAgent 工具（违反 Forbidden Patterns 第 8 条「不绕过 MCP
   直连 RAG 内部机制」）。
5. 每个工具 description 必含「用于：」边界行——五要素之一，未交代使用场景会让模型乱调。

测试隔离：registry 是进程级 singleton，本套测试用「清空重建 + 退出恢复快照」
fixture 保证白名单断言不受执行顺序影响、也不泄漏给其他测试（review 第 1 轮 P2）。

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

# 必须 source="mcp" 的工具——正路经 MCP dispatcher（search_dictionary 通道）。
MCP_SOURCED_TOOLS: frozenset[str] = frozenset(
    {"search_tables", "get_table_ddl"}
)

# 反向钉：无 MCP 等价工具、正路 HTTP 直连的 schema 工具，source 必须 == "local"。
LOCAL_SOURCED_TOOLS: frozenset[str] = frozenset({"list_tables"})

ALLOWED_SOURCE_VALUES: frozenset[str] = frozenset({"local", "mcp"})


@pytest.fixture
def registered() -> None:
    """把 registry 构造成确定性状态，测试间零全局耦合。

    registry 是进程级 singleton（app.tools.registry.registry）。仅「快照 + 恢复」
    防不住别的测试先行注册临时工具造成的污染（白名单断言依赖执行顺序），所以
    进入时 **清空重建**（clear + register_all_tools → 注册面恰好等于白名单），
    退出时恢复原快照不泄漏给其他测试（review 第 1 轮 P2）。
    register_all_tools 幂等，重复调用安全。
    """
    tools_snapshot = dict(registry._tools)
    instances_snapshot = dict(registry._instances)
    try:
        registry._tools.clear()
        registry._instances.clear()
        register_all_tools()
        yield
    finally:
        registry._tools.clear()
        registry._tools.update(tools_snapshot)
        registry._instances.clear()
        registry._instances.update(instances_snapshot)


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
# 钉子 2：source 字段全局约束 + MCP-first 工具标 "mcp" / HTTP 直连工具标 "local"
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


def test_mcp_first_tools_marked_as_mcp_source(registered):
    """search_tables / get_table_ddl 正路经 MCP dispatcher（ragent-py
    search_dictionary 检索通道），故 metadata.source 必须 == "mcp"。

    source 语义（review 第 1 轮决议）：**该 Tool 请求满足时的实际正路 runtime
    通道**。source 标记让审计/限流/trace 识别 MCP 通道调用——标错即观测信息错。

    注意 list_tables 不在此列：它无 MCP 等价工具，正路是 _list_dict_docs
    HTTP 直连（见反向钉子）。search_interface_dictionary / search_faq 同为
    MCP-first，但 source 标注策略待 Task 4 mcp-contract.md 定夺。"""
    bad: list[str] = []
    for name in MCP_SOURCED_TOOLS:
        meta = registry.get_metadata(name)
        assert meta is not None, f"{name} 未注册"
        if meta.source != "mcp":
            bad.append(f"{name}: source={meta.source!r}（期望 'mcp'）")
    assert not bad, (
        "MCP-first 工具必须 source='mcp'（正路走 MCP dispatcher）:\n"
        + "\n".join(bad)
    )


def test_http_direct_tools_stay_local_source(registered):
    """反向钉：list_tables 无 MCP 等价工具，正路是 rag_schema._list_dict_docs
    HTTP 直连（不经 dispatcher），metadata.source 必须 == "local"。

    review 第 1 轮 P1：把 HTTP 直连工具标成 source="mcp" 是 metadata 对 runtime
    行为的错误描述——trace 里会看到「tool=list_tables source=mcp」但实际没有
    任何 MCP 调用。plan Step 2 原文「schema 三工具 source=='mcp'」写宽了，
    以 runtime truth 为准（rag_schema.py:16/:157 明确 list_tables 维持 HTTP 直连）。
    """
    bad: list[str] = []
    for name in LOCAL_SOURCED_TOOLS:
        meta = registry.get_metadata(name)
        assert meta is not None, f"{name} 未注册"
        if meta.source != "local":
            bad.append(f"{name}: source={meta.source!r}（期望 'local'，正路 HTTP 直连）")
    assert not bad, (
        "HTTP 直连工具的 source 不许谎报为 'mcp'（review 第 1 轮 P1）:\n"
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