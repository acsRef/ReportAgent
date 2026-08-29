"""P4c Task 2: ContextRuntime 接入后主链不能 break.

钉 3 件事:
1) Requirement Agent 入口 _requirement_parse 跑通后 bundle.conversation_context 非空
2) Confirmed Execution Agent 入口 _confirmed_sql_agent 跑通后 bundle.conversation_context 非空
3) selective policy 启动后 recall_items 透传到 assembled_context

策略: monkeypatch app.context.runtime.prepare_conversation_context 与
       app.memory.semantic.recall_structured 让 ContextRuntime.build() 在不依赖
       DATABASE_URL 的纯逻辑下跑通、断言拼装产出。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.context.runtime import ContextRuntime
from app.context.decision import SelectiveRecallPolicy
from app.memory import semantic as semantic_memory
from app.memory import query as query_memory


@pytest.fixture(autouse=True)
def _noop_memory_recall(monkeypatch):
    """防 Default LegacyFallbackPolicy 触发真 MemoryManager（在无 DATABASE_URL 时 get_pool 抛错）。"""
    async def noop_recall(q, uid, **_):
        return []

    monkeypatch.setattr(semantic_memory, "recall_structured", noop_recall)
    monkeypatch.setattr(query_memory, "recall_structured", noop_recall)


@pytest.mark.asyncio
async def test_requirement_agent_entry_has_conversation_context(monkeypatch):
    """_requirement_parse 入口: ContextRuntime.build() 返回 bundle['conversation_context'] 非空."""
    async def fake_prepare(sid, uid):
        return "<L1>history</L1><L2>summary</L2>"

    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare
    )
    # 即便 selective strategy 启动,也要求 conversation 字段被填——selective 仅
    # 控制 recall items,conversation 来自 conversation engine glue.
    bundle = await ContextRuntime().build(
        session_id="session-A", user_id=1,
        query="上月销售", agent="requirement_analyze",
    )
    assert bundle["conversation_context"]
    assert "history" in bundle["conversation_context"]
    assert bundle["agent_policy"] == "requirement"


@pytest.mark.asyncio
async def test_confirmed_execution_agent_entry_has_conversation_context(monkeypatch):
    """_confirmed_sql_agent 入口: ContextRuntime.build() 返回 bundle.conversation_context 非空."""
    async def fake_prepare(sid, uid):
        return "<L2>confirmed-context</L2>"

    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare
    )
    bundle = await ContextRuntime().build(
        session_id="session-B", user_id=1,
        query="再按产品细分", agent="confirmed_execution_sql_agent",
    )
    assert bundle["conversation_context"]
    assert "confirmed-context" in bundle["conversation_context"]
    assert bundle["agent_policy"] == "execution"


@pytest.mark.asyncio
async def test_selective_policy_injects_recall_into_assembled(monkeypatch):
    """SelectiveRecallPolicy 启动: decision.semantic=True 时 assembled_context 包含 recall_items raw_text."""
    async def fake_prepare(sid, uid):
        return "<L1>conv</L1>"

    async def fake_recall(q, uid, *, top_k_preferences=3):
        return [{
            "raw_text": "user prefers bar charts",
            "source": "memory_semantic",
            "kind": "preference",
            "score": 0.9,
            "ref_id": 42,
        }]

    # 主动调 ContextRuntime(policy=SelectiveRecallPolicy()) 直接验证 selective policy 行为.
    # requirement_analyze + 含 _PREF_TASK 关键词("再按产品细分"含 "再按" 非 _PREF_TASK; 改 query 含 "图表" 触 _PREF_TASK)
    monkeypatch.setattr(
        "app.context.runtime.prepare_conversation_context", fake_prepare
    )
    monkeypatch.setattr(semantic_memory, "recall_structured", fake_recall)

    bundle = await ContextRuntime(policy=SelectiveRecallPolicy()).build(
        session_id="s", user_id=1,
        query="以后都用图表展示", agent="requirement_analyze",
    )
    assert "user prefers bar charts" in bundle["assembled_context"], (
        f"assembled_context 未注入 recall_items raw_text: {bundle['assembled_context']!r}"
    )
    assert bundle["recall_items"][0]["source"] == "memory_semantic"
