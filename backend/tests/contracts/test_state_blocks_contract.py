"""State 五块 contract 钉子（P3 Task 1）。

P3 plan §2.3 钉住：
- 五块 TypedDict 字段名与 state-contract.md §一一字一致
- split_state 仅做 deterministic 1:1 映射（review P1 #3 决议）
- unmapped 字段保留（不强行归类）
- merge_state 重建原 state dict 键集合 + 值
- TypedDict 是 static ownership contract，不是 runtime enforcement

本文件只钉 4 条有生产含义的契约：字段所有权 / 无损投影 / v1 checkpoint 投影 /
禁止语义伪映射。恒等映射之类的「抄映射表」用例已被上述四条覆盖。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.state.blocks import (
    ExecutionState,
    ReportState,
    RequestState,
    RequirementState,
    RuntimeState,
    merge_state,
    split_state,
)


def test_five_blocks_field_ownership():
    """五块 TypedDict 字段名与 state-contract.md §一所有权表一字一致。"""
    ownership = [
        (RequestState, {
            "request_id", "session_id", "user_id", "original_query", "current_query",
        }),
        (RequirementState, {
            "normalized_query", "schema_candidates", "requirement_card",
            "missing_dimensions", "clarification_history", "confirmation_status",
        }),
        (ExecutionState, {
            "confirmed_requirement", "schema_context", "query_plan",
            "generated_sql", "validation_result", "query_result",
            "execution_status", "error", "retry_count",
        }),
        (ReportState, {
            "report_spec", "report_version", "chart_config", "insight",
        }),
        (RuntimeState, {
            "trace_id", "active_agent", "memory_context", "tool_calls", "mcp_calls",
        }),
    ]
    for block_cls, expected in ownership:
        assert set(block_cls.__annotations__) == expected, (
            f"{block_cls.__name__} 字段集合与 state-contract.md §一 不一致"
        )


def test_split_merge_is_lossless_view():
    """split → merge 是无损投影：五块名齐全、键集合/值/unmapped 一个不丢。"""
    # v2 shape：split 后 merge 回来必须与输入完全相同
    v2_states = [
        {
            "session_id": "sess-1",
            "user_id": 42,
            "trace_id": "t-1",
            "memory_context": "ctx",
            "intent": "report",        # unmapped
            "user_query": "销售",       # unmapped
        },
        {
            "session_id": "sess-1",
            "intent": "report",          # unmapped
            "active_agent": "execution",  # v2 名
        },
        {},
    ]
    for state in v2_states:
        blocks, unmapped = split_state(state)
        assert set(blocks.keys()) == {
            "request", "requirement", "execution", "report", "runtime",
        }, "split_state 必须返回五块名"
        merged = merge_state(blocks, unmapped=unmapped)
        assert set(merged.keys()) == set(state.keys()), f"往返丢键: {state}"
        assert merged == state, f"往返丢值: {state}"

    # unmapped 三兄弟（plan §2.3 unmapped 表）不得被强行归类：块全空、原样保留
    unmapped_only = {
        "user_query": "2024 销售",
        "intent": "report",
        "retry_counters": {"repair": 2},
    }
    blocks, unmapped = split_state(unmapped_only)
    assert unmapped == unmapped_only
    for name in blocks:
        assert blocks[name] == {}, f"unmapped 字段被强行归类进 {name}"


def test_v1_checkpoint_projects_to_v2_field_names():
    """v1 checkpoint 输入经 split 得到 v2 字段名的 view（B7 恢复路径的唯一映射钉子）。

    v1 源字段（active_sub_agent / insight_text）是 _MAPPED_SOURCE_FIELDS 成员：
    rename 后进目标块，不进 unmapped。
    """
    v1_cases = [
        (
            {"trace_id": "t-1", "active_sub_agent": "execution", "memory_context": "ctx"},
            "runtime",
            {"trace_id": "t-1", "active_agent": "execution", "memory_context": "ctx"},
        ),
        (
            {"report_spec": {"title": "x"}, "chart_config": {"type": "bar"},
             "insight_text": "华东领先"},
            "report",
            {"report_spec": {"title": "x"}, "chart_config": {"type": "bar"},
             "insight": "华东领先"},
        ),
        ({"active_sub_agent": "execution"}, "runtime", {"active_agent": "execution"}),
    ]
    for v1_state, block_name, expected in v1_cases:
        blocks, unmapped = split_state(v1_state)
        assert blocks[block_name] == expected, f"v1 → v2 投影错误: {v1_state}"
        assert unmapped == {}, f"v1 源字段不应落入 unmapped: {v1_state}"


def test_no_semantic_pseudo_mapping():
    """review P1 #3：只允许同名/同类型 rename，禁止语义猜测式转换。"""
    pseudo_pairs = [
        # intent 不能被映射成 normalized_query
        ({"intent": "report"}, "requirement", "normalized_query"),
        # clarification_context: dict → clarification_history: list，类型不同
        ({"clarification_context": {"foo": "bar"}}, "requirement", "clarification_history"),
        # retry_counters: dict → retry_count: int，取哪个 counter？总和？无 deterministic
        ({"retry_counters": {"repair": 2}}, "execution", "retry_count"),
    ]
    for state, block_name, forbidden_field in pseudo_pairs:
        blocks, unmapped = split_state(state)
        assert forbidden_field not in blocks[block_name], (
            f"{forbidden_field} 被伪映射了：{state}"
        )
        assert unmapped == state, f"语义伪映射源字段必须原样留在 unmapped: {state}"
