"""SQL-gate enforcement test for the requirement-analysis graph.

This test asserts the critical property of the requirement-analysis flow:
the schema-only graph must NEVER call validate_sql, execute_sql, or any
report tool. The check is structural — we monkeypatch the dangerous
functions to raise if invoked, then drive the graph end-to-end.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.graphs

from app.models.requirement import RequirementCard, RequirementAssumption
from app.agent.requirement_analysis_graph import build_requirement_analysis_graph


def _tripwire(name: str):
    """Return a function that raises if called."""
    def _explode(*args, **kwargs):
        raise AssertionError(
            f"SQL/Report gate violated: {name}() was called from the "
            f"requirement-analysis graph (must be unreachable)"
        )
    return _explode


@pytest.fixture
def sql_gate(monkeypatch):
    """Patch the SQL/Report tool entry points to trip if called."""
    import app.tools.sql_tools as sql_tools_mod
    import app.tools.report_tools as report_tools_mod

    monkeypatch.setattr(sql_tools_mod, "validate_sql", _tripwire("validate_sql"))
    monkeypatch.setattr(sql_tools_mod, "execute_sql", _tripwire("execute_sql"))
    monkeypatch.setattr(sql_tools_mod, "chart_advisor", _tripwire("chart_advisor"))
    monkeypatch.setattr(sql_tools_mod, "insight_analyst", _tripwire("insight_analyst"))
    monkeypatch.setattr(
        report_tools_mod, "trend_analysis", _tripwire("trend_analysis"),
    )
    monkeypatch.setattr(
        report_tools_mod, "group_compare", _tripwire("group_compare"),
    )
    monkeypatch.setattr(
        report_tools_mod, "detect_anomaly", _tripwire("detect_anomaly"),
    )


def test_requirement_analysis_graph_does_not_call_sql_or_report_tools(
    monkeypatch, sql_gate,
) -> None:
    """Drive the requirement-analysis graph end-to-end and assert the SQL
    gate holds (no tripwires fire). LLM is stubbed; data_agent and
    persist_draft are stubbed to avoid network/DB dependencies (this
    test is purely about the structural gate).
    """
    # Stub the LLM so we don't hit the real API.
    import app.agent.requirement_parser as parser_mod
    monkeypatch.setattr(parser_mod, "call_llm", lambda *a, **k: '{"summary":"x","target_metrics":[],"time_range":null,"scope":[],"dimensions":[],"analysis_methods":[],"confidence":0.9,"missing_fields":[],"assumptions":[]}')

    # Stub the data_agent's MCP path so no real network call happens.
    from app.agent import data_graph as data_graph_mod
    from app.models.contracts import SchemaContext, TableSchema
    fake_schema = SchemaContext(
        tables=[TableSchema(name="fact_sales", description="sales")],
        confidence=1.0,
    )

    async def fake_data_ainvoke(_state: dict) -> dict:
        return {"schema_context": fake_schema}

    monkeypatch.setattr(data_graph_mod, "build_data_graph", lambda: type("G", (), {"ainvoke": staticmethod(fake_data_ainvoke)})())

    # Stub persist_draft so we don't need a live PG pool for this structural
    # test. The point is "no SQL/Report tools are reachable", not "writes
    # work end-to-end".
    import app.agent.requirement_analysis_graph as rag_mod

    async def fake_persist(state: dict) -> dict:
        return {"draft_id": 0, "execution_status": "SUCCESS"}

    monkeypatch.setattr(rag_mod, "_persist_draft", fake_persist)

    # Now run the graph
    from app.agent.requirement_analysis_graph import build_requirement_analysis_graph
    graph = build_requirement_analysis_graph()

    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    state = {
        "user_query": "2024 华东销售额趋势",
        "user_id": 1,
        "session_id": f"test-{uuid.uuid4()}",
        "trace_id": "trace-test",
        "schema_context": None,
        "requirement_card": None,
        "draft_id": None,
        "security_score": 0,
        "security_level": "LOW",
        "security_warning": "",
        "error": None,
        "execution_status": "RUNNING",
    }
    result = asyncio.run(graph.ainvoke(state, config))
    assert result["execution_status"] in ("SUCCESS", "PERSIST_FAILED")
    # If we got here, no tripwire fired.


def test_dictionary_lookup_degrades_without_ragent(monkeypatch, sql_gate) -> None:
    """RAGENT_URL 未配置：字典检索静默降级，需求分析全链路仍完成。

    验证 B5 接线：
      - `_requirement_parse` 调一次字典检索（tool invoke）；
      - 失败降级为 None，不阻塞 parse_requirement；
      - 整图 SQL 门控依旧（tripwire 不触发）。

    P2 Task 2 增量：MCP-first + flag-gated fallback——mock MCP 失败让 dispatcher
    走 HTTP fallback；HTTP 路径因 RAGENT_URL 未配置返回「未配置」error，parse 仍 None。
    """
    # 1. RAGENT_URL 未配置 → 字典工具返回 error 字段、matches=空；解析逻辑必须不抛。
    monkeypatch.delenv("RAGENT_URL", raising=False)

    # 1b. MCP-first 路径在测试环境强制失败（不起真子进程）。
    from unittest.mock import MagicMock
    from app.tools import register_all_tools
    from app.tools.registry import registry
    from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

    register_all_tools()
    fake_mcp = MagicMock()
    fake_mcp.call_tool.side_effect = MCPBoundaryError(
        MCPErrorCode.MCP_UNAVAILABLE, "test-forces-fallback"
    )
    # 直接 patch registry 里的 instance，避免起 ragent-py subprocess
    monkeypatch.setitem(registry._instances, "search_interface_dictionary",
                        SimpleNamespace(invoke=lambda _payload: json.dumps(
                            {"error": "字典服务未配置（RAGENT_URL 为空）"}, ensure_ascii=False,
                        )))

    # 2. Stub data_agent 走 schema-only 假通路
    from app.agent import data_graph as data_graph_mod
    from app.models.contracts import SchemaContext, TableSchema

    fake_schema = SchemaContext(
        tables=[TableSchema(name="fact_sales", description="sales")],
        confidence=1.0,
    )

    async def fake_data_ainvoke(_state: dict) -> dict:
        return {"schema_context": fake_schema}

    monkeypatch.setattr(
        data_graph_mod, "build_data_graph",
        lambda: type("G", (), {"ainvoke": staticmethod(fake_data_ainvoke)})(),
    )

    # 3. Stub persist_draft 避免 DB 依赖
    import app.agent.requirement_analysis_graph as rag_mod

    async def fake_persist(state: dict) -> dict:
        return {"draft_id": 0, "execution_status": "SUCCESS"}

    monkeypatch.setattr(rag_mod, "_persist_draft", fake_persist)

    # 4. Spy `parse_requirement`：捕获 keyword args，确认 `dictionary_context` 存在
    captured: dict = {}

    # `dictionary_context` 不给默认值——强制要求生产代码显式传入；
    # 这样 B5 之前会 TypeError，B5 之后才会进入断言。
    def spy_parse_requirement(*, user_query, schema_context, prior_card=None,
                              conversation_context=None, dictionary_context,
                              assembled_context=None):  # P4c: spy 同步加 kwarg
        captured["user_query"] = user_query
        captured["schema_context"] = schema_context
        captured["prior_card"] = prior_card
        captured["conversation_context"] = conversation_context
        captured["dictionary_context"] = dictionary_context
        captured["assembled_context"] = assembled_context
        return RequirementCard(
            id="t", status="missing", summary="s",
            missing_fields=[],
            assumptions=[],
            confidence=0.0,
        )

    monkeypatch.setattr(rag_mod, "parse_requirement", spy_parse_requirement)

    # 5. 跑全图
    graph = build_requirement_analysis_graph()
    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    state = {
        "user_query": "统计订单推送的 amt 总额",
        "user_id": 1,
        "session_id": f"test-{uuid.uuid4()}",
        "trace_id": "",
        "schema_context": None,
        "requirement_card": None,
        "draft_id": None,
        "security_score": 0,
        "security_level": "LOW",
        "security_warning": "",
        "error": None,
        "execution_status": "RUNNING",
    }
    # ragent 未配置 → 无字典命中 → 意图分类走 LLM 兜底；mock 成 report 保证离线确定。
    import app.agent.intent as intent_mod
    monkeypatch.setattr(
        intent_mod, "call_llm",
        lambda prompt, **kw: '{"kind": "report", "confidence": 0.9, "reason": "test"}',
    )
    result = asyncio.run(graph.ainvoke(state, config))

    # 断言 spy 收到 `dictionary_context` 关键字（值在 RAGENT_URL 缺失时为 None 或 ""）
    assert "dictionary_context" in captured, (
        "_requirement_parse 没把字典上下文传进 parse_requirement"
    )
    assert captured["dictionary_context"] in (None, ""), (
        f"未配置 RAGENT_URL 时字典上下文应降级为 None/空串，"
        f"实际={captured['dictionary_context']!r}"
    )
    # 整图完成；SQL 门控由 sql_gate fixture 守住
    assert result.get("requirement_card") is not None
    assert result["execution_status"] in ("SUCCESS", "PERSIST_FAILED")


def test_dictionary_lookup_passes_hits_to_parse_requirement(monkeypatch, sql_gate) -> None:
    """字典命中 → `_requirement_parse` 把序列化片段注入 parse_requirement。

    验证 B5 接线的「正常路径」：用 stub 替代 httpx，让 tool 返回受控的
    matches 集合，再断言 spy 收到的 `dictionary_context` 字符串包含
    期望的 source/text 片段，并遵守单条 300 字符截断。
    """
    # 1. 配置 RAGENT_URL，让 tool 进入 httpx 路径
    monkeypatch.setenv("RAGENT_URL", "http://fake:8000")
    monkeypatch.setenv("RAGENT_USER", "admin")
    monkeypatch.setenv("RAGENT_PASSWORD", "admin123")
    monkeypatch.setenv("DICT_KB_NAME", "数据字典")

    # 2. Stub httpx 返回登录 + kb list + 命中 items。复用 contracts 测试里
    #    的 _Resp 套路，避开 @tool 包装对函数 globals 的潜在影响。
    import app.tools.interface_dict_tools as dict_mod

    # P2 Task 2：graph 改走 registry——确保 search_interface_dictionary 已注册；
    # 并强制 MCP 失败让 dispatcher 走 HTTP fallback（既有 httpx stub 接管）。
    from unittest.mock import MagicMock
    from app.tools import register_all_tools
    from app.tools.registry import registry
    from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

    register_all_tools()
    fake_mcp = MagicMock()
    fake_mcp.call_tool.return_value = {
        "matches": [
            {
                "chunk_id": "c1", "document_id": "d1",
                "text": "total_amount: 订单推送的销售金额（不含税）" * 5,
                "title": "dict-table_public_fact_sales.md",
                "section_path": "", "score": 0.9,
            },
            {
                "chunk_id": "c2", "document_id": "d2",
                "text": "amt 是 total_amount 的简写",
                "title": "dict-fields.md",
                "section_path": "", "score": 0.7,
            },
        ]
    }
    monkeypatch.setattr(dict_mod, "get_rag_mcp_client", lambda: fake_mcp)
    dict_mod._token_cache.clear()
    dict_mod._kb_id_cache.clear()

    # 3. Stub data_agent + persist_draft
    from app.agent import data_graph as data_graph_mod
    from app.models.contracts import SchemaContext, TableSchema

    fake_schema = SchemaContext(
        tables=[TableSchema(name="fact_sales", description="sales")],
        confidence=1.0,
    )

    async def fake_data_ainvoke(_state: dict) -> dict:
        return {"schema_context": fake_schema}

    monkeypatch.setattr(
        data_graph_mod, "build_data_graph",
        lambda: type("G", (), {"ainvoke": staticmethod(fake_data_ainvoke)})(),
    )

    import app.agent.requirement_analysis_graph as rag_mod

    async def fake_persist(state: dict) -> dict:
        return {"draft_id": 0, "execution_status": "SUCCESS"}

    monkeypatch.setattr(rag_mod, "_persist_draft", fake_persist)

    # 4. Spy parse_requirement
    captured: dict = {}

    def spy_parse_requirement(*, user_query, schema_context, prior_card=None,
                              conversation_context=None, dictionary_context=None,
                              assembled_context=None):  # P4c: spy 同步加 kwarg
        captured["dictionary_context"] = dictionary_context
        captured["assembled_context"] = assembled_context
        return RequirementCard(
            id="t", status="missing", summary="s",
            missing_fields=[],
            assumptions=[],
            confidence=0.0,
        )

    monkeypatch.setattr(rag_mod, "parse_requirement", spy_parse_requirement)

    # 5. 跑图
    graph = build_requirement_analysis_graph()
    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    state = {
        "user_query": "total_amount 是什么",
        "user_id": 1,
        "session_id": f"test-{uuid.uuid4()}",
        "trace_id": "",
        "schema_context": None,
        "requirement_card": None,
        "draft_id": None,
        "security_score": 0,
        "security_level": "LOW",
        "security_warning": "",
        "error": None,
        "execution_status": "RUNNING",
    }
    result = asyncio.run(graph.ainvoke(state, config))

    ctx = captured.get("dictionary_context")
    assert isinstance(ctx, str) and ctx, "字典命中时 dictionary_context 应为非空字符串"
    # 两条命中 source 必须出现
    assert "dict-table_public_fact_sales.md" in ctx
    assert "dict-fields.md" in ctx
    # 单条 text 截到 300 字符（用 [:300] 而非 400）
    for line in ctx.splitlines():
        if line.startswith("- "):
            # 去掉 "- source: " 前缀，剩下的 text 段
            payload = line[len("- source: "):]
            # payload 格式 "- source: text[:300]"
            # 取出 text 部分（source 含路径，可能含冒号）
            colon = payload.find(":")
            text_part = payload[colon + 1:].strip() if colon != -1 else payload
            assert len(text_part) <= 300, (
                f"单条 text 超过 300 字符截断：{len(text_part)}"
            )
    # 整图完成
    assert result["execution_status"] in ("SUCCESS", "PERSIST_FAILED")
