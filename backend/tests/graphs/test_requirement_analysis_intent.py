"""需求分析图：工作流式意图路由测试（REPORT / CHITCHAT / INTERFACE）。

classify_intent 用 mock 固定（其内部逻辑在 tests/smoke/test_intent.py 单测），
这里专注验证路由：意图 → 正确的节点 → 产出。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agent.intent import IntentKind, IntentResult
from app.agent.requirement_analysis_graph import build_requirement_analysis_graph

pytestmark = pytest.mark.graphs


def _build_graph(monkeypatch: pytest.MonkeyPatch, intent: IntentKind, spy=None):
    """搭一个意图固定的需求分析图；heavy 依赖全 stub。"""
    import app.agent.requirement_analysis_graph as rag_mod

    async def fake_data(_state):
        from app.models.contracts import SchemaContext, TableSchema
        return {"schema_context": SchemaContext(tables=[TableSchema(name="fact_sales", description="s")])}

    monkeypatch.setattr(rag_mod, "classify_intent",
                        lambda *a, **k: IntentResult(intent, "test", 0.9))

    async def _fake_dict(q):
        return (False, "")
    monkeypatch.setattr(rag_mod, "_fetch_dict_context", _fake_dict)
    monkeypatch.setattr(rag_mod, "build_data_graph",
                        lambda: type("G", (), {"ainvoke": staticmethod(fake_data)})())

    async def fake_persist(state):
        if spy is not None:
            spy("persist_draft")
        return {"draft_id": 1, "execution_status": "SUCCESS"}

    monkeypatch.setattr(rag_mod, "_persist_draft", fake_persist)

    def fake_parse(**kw):
        if spy is not None:
            spy("parse_requirement")
        from app.models.requirement import RequirementCard
        return RequirementCard(id="t", status="complete", summary="s", confidence=1.0)

    monkeypatch.setattr(rag_mod, "parse_requirement", fake_parse)
    graph = build_requirement_analysis_graph()
    config = {"configurable": {"thread_id": f"intent-{uuid.uuid4()}"}}
    return graph, config


def _base_state():
    return {
        "user_query": "各区域销售额",
        "user_id": 1,
        "session_id": f"test-{uuid.uuid4()}",
        "trace_id": "t",
        "schema_context": None,
        "requirement_card": None,
        "draft_id": None,
        "security_score": 0,
        "security_level": "LOW",
        "security_warning": "",
        "error": None,
        "intent": None,
        "intent_reason": None,
        "casual_reply": None,
        "dict_context": None,
        "execution_status": "RUNNING",
    }


def test_report_intent_builds_card(monkeypatch):
    calls: list[str] = []
    graph, config = _build_graph(monkeypatch, IntentKind.REPORT, spy=lambda n: calls.append(n))
    result = asyncio.run(graph.ainvoke(_base_state(), config))
    assert "parse_requirement" in calls
    assert "persist_draft" in calls
    assert result.get("requirement_card") is not None
    assert result.get("casual_reply") is None


def test_chitchat_intent_casual_no_card(monkeypatch):
    calls: list[str] = []
    graph, config = _build_graph(monkeypatch, IntentKind.CHITCHAT, spy=lambda n: calls.append(n))
    result = asyncio.run(graph.ainvoke(_base_state(), config))
    assert "parse_requirement" not in calls
    assert "persist_draft" not in calls
    assert result.get("requirement_card") is None
    assert result.get("casual_reply")


def test_interface_intent_builds_stream_card(monkeypatch):
    calls: list[str] = []
    graph, config = _build_graph(monkeypatch, IntentKind.INTERFACE, spy=lambda n: calls.append(n))
    result = asyncio.run(graph.ainvoke(_base_state(), config))
    assert "parse_requirement" not in calls
    assert "persist_draft" in calls
    card = result.get("requirement_card")
    assert card is not None
    assert any(getattr(a, "key", "") == "data_source:stream" for a in card.assumptions)