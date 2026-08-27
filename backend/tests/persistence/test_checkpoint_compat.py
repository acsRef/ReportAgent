"""P3 端到端 checkpoint compat 集成测试（Task 3 补充）。

验证 (γ) graph 入口节点 migrate_checkpoint 注入在真实 LangGraph 跑通。

设计边界（与 plan §2.4 (γ) 折中说明对齐）：
- migrate_checkpoint 给入口节点提供 v2 view；entry 函数返回的 update dict 由
  LangGraph 默认 reducer 合入 state，**不**删除未写回字段。所以 input 阶段
  v1 名（active_sub_agent / insight_text）可能仍存于合并后 state——这是
  LangGraph 状态合并语义，不是 migrate 缺陷。
- 真实 graph 节点（_security_guard / _plan 等）只访问 unmapped 字段（user_query /
  session_id / schema_context 等）或同名字段，不依赖 v1 名被删；contract §一零侵入意图保持。

dev 友好：用 MemorySaver（不依赖 DATABASE_URL）；真实 AsyncPostgresSaver 走同一
LangGraph checkpointer 协议，结果等价。
"""
from __future__ import annotations

import uuid
from typing import TypedDict

import pytest

pytestmark = pytest.mark.contracts


class _CompatState(TypedDict, total=False):
    original_query: str
    active_sub_agent: str
    active_agent: str
    insight_text: str
    insight: str
    schema_version: str


def _make_entry():
    """入口节点：调 migrate_checkpoint，把 v2 view 关键字段写入 update dict。"""
    from app.state.checkpoint_adapter import migrate_checkpoint

    async def _entry(state: _CompatState) -> dict:
        migrated = migrate_checkpoint(dict(state))
        # 返回 update dict 含 v2 名（active_agent / insight / schema_version）
        # 不动 v1 名字段（LangGraph 默认 reducer 保留它们）
        update: dict = {"schema_version": migrated["schema_version"]}
        if "active_agent" in migrated:
            update["active_agent"] = migrated["active_agent"]
        if "insight" in migrated:
            update["insight"] = migrated["insight"]
        return update

    return _entry


async def _run_graph(thread_id: str, input_state: dict):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    saver = MemorySaver()
    g = StateGraph(_CompatState)
    g.add_node("entry", _make_entry())
    g.set_entry_point("entry")
    g.add_edge("entry", END)
    graph = g.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": thread_id}}
    return await graph.ainvoke(input_state, config)


@pytest.mark.asyncio
async def test_v1_shape_input_triggers_migrate_to_v2_view():
    """v1 shape input → entry 调 migrate → update dict 含 v2 名 + schema_version。"""
    from app.state.checkpoint_adapter import CURRENT_SCHEMA_VERSION

    result = await _run_graph(
        f"p3-e2e-{uuid.uuid4()}",
        {
            "original_query": "x",
            "active_sub_agent": "y",
            "insight_text": "华东",
        },
    )
    assert result.get("schema_version") == CURRENT_SCHEMA_VERSION
    # entry 把 v2 名写入 update dict → 合并进 state
    assert result.get("active_agent") == "y"
    assert result.get("insight") == "华东"


@pytest.mark.asyncio
async def test_fresh_input_no_marker_gets_schema_version_injected():
    """(γ) 折中：graph 入口 fresh input（无 v1 marker 无 schema_version）→
    migrate_checkpoint 不 raise，inject schema_version=v2。"""
    from app.state.checkpoint_adapter import CURRENT_SCHEMA_VERSION

    result = await _run_graph(f"p3-e2e-{uuid.uuid4()}", {})
    assert result.get("schema_version") == CURRENT_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_v2_passthrough_does_not_re_rename():
    """v2 marker 已存在 → migrate_checkpoint 透传 → entry 仅看到 v2 名。"""
    from app.state.checkpoint_adapter import CURRENT_SCHEMA_VERSION

    result = await _run_graph(
        f"p3-e2e-{uuid.uuid4()}",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "active_agent": "execution",
        },
    )
    assert result.get("schema_version") == CURRENT_SCHEMA_VERSION
    assert result.get("active_agent") == "execution"
    # 没有 v1 marker 输入 → update dict 不引入 active_sub_agent
    assert "active_sub_agent" not in result
