"""State 五块 contract 钉子（P3 Task 1）。

P3 plan §2.3 钉住：
- 五块 TypedDict 字段名与 state-contract.md §一一字一致
- split_state 仅做 deterministic 1:1 映射（review P1 #3 决议）
- unmapped 字段保留（不强行归类）
- merge_state 重建原 state dict 键集合 + 值
- TypedDict 是 static ownership contract，不是 runtime enforcement
"""
from __future__ import annotations

import pytest

from app.state.blocks import (
    ExecutionState,
    ReportState,
    RequestState,
    RequirementState,
    RuntimeState,
    merge_state,
    split_state,
)


class TestStateBlockFields:
    """五块 TypedDict 字段名与 state-contract.md §一一字一致。"""

    def test_request_state_fields(self):
        assert set(RequestState.__annotations__) == {
            "request_id", "session_id", "user_id", "original_query", "current_query"
        }

    def test_requirement_state_fields(self):
        assert set(RequirementState.__annotations__) == {
            "normalized_query", "schema_candidates", "requirement_card",
            "missing_dimensions", "clarification_history", "confirmation_status",
        }

    def test_execution_state_fields(self):
        assert set(ExecutionState.__annotations__) == {
            "confirmed_requirement", "schema_context", "query_plan",
            "generated_sql", "validation_result", "query_result",
            "execution_status", "error", "retry_count",
        }

    def test_report_state_fields(self):
        assert set(ReportState.__annotations__) == {
            "report_spec", "report_version", "chart_config", "insight",
        }

    def test_runtime_state_fields(self):
        assert set(RuntimeState.__annotations__) == {
            "trace_id", "active_agent", "memory_context", "tool_calls", "mcp_calls",
        }


class TestSplitStateDeterministicMapping:
    """split_state 按 deterministic 映射表投影；unmapped 保留。"""

    def test_split_extracts_request_state_from_named_fields(self):
        # plan §2.3 表第一行：original_query / current_query / session_id / user_id
        legacy = {
            "original_query": "2024 销售",
            "current_query": "2024 销售趋势",
            "session_id": "sess-1",
            "user_id": 42,
        }
        blocks, unmapped = split_state(legacy)
        assert blocks["request"] == legacy
        assert unmapped == {}

    def test_split_extracts_runtime_state_with_rename(self):
        # active_sub_agent → active_agent (deterministic rename)
        # trace_id / memory_context 同名同类型
        legacy = {
            "trace_id": "t-1",
            "active_sub_agent": "execution",
            "memory_context": "ctx",
        }
        blocks, unmapped = split_state(legacy)
        assert blocks["runtime"] == {
            "trace_id": "t-1",
            "active_agent": "execution",
            "memory_context": "ctx",
        }
        assert unmapped == {}

    def test_split_extracts_report_state_with_rename(self):
        # insight_text → insight (deterministic rename)
        legacy = {
            "report_spec": {"title": "x"},
            "chart_config": {"type": "bar"},
            "insight_text": "华东领先",
        }
        blocks, unmapped = split_state(legacy)
        assert blocks["report"] == {
            "report_spec": {"title": "x"},
            "chart_config": {"type": "bar"},
            "insight": "华东领先",
        }
        assert unmapped == {}

    def test_split_keeps_unmapped_fields(self):
        # plan §2.3 unmapped 表：intent / user_query / retry_counters 都是 unmapped
        state = {
            "user_query": "2024 销售",
            "intent": "report",
            "retry_counters": {"repair": 2},
        }
        blocks, unmapped = split_state(state)
        assert unmapped == state
        for name in blocks:
            assert blocks[name] == {}

    def test_split_does_not_pseudo_map_intent_to_normalized_query(self):
        # review P1 #3 关键钉子：intent 不能被映射到 normalized_query
        state = {"intent": "report"}
        blocks, unmapped = split_state(state)
        assert "normalized_query" not in blocks["requirement"]
        assert unmapped["intent"] == "report"

    def test_split_does_not_auto_convert_clarification_context(self):
        # review P1 #3：clarification_context: dict → clarification_history: list
        # 类型不同，无 deterministic conversion → 保留 unmapped
        state = {"clarification_context": {"foo": "bar"}}
        blocks, unmapped = split_state(state)
        assert "clarification_history" not in blocks["requirement"]
        assert unmapped["clarification_context"] == {"foo": "bar"}

    def test_split_does_not_auto_convert_retry_counters_to_retry_count(self):
        # review P1 #3：retry_counters: dict → retry_count: int 无 deterministic
        # conversion（取哪个 counter？总和？）→ 保留 unmapped
        state = {"retry_counters": {"repair": 2}}
        blocks, unmapped = split_state(state)
        assert "retry_count" not in blocks["execution"]
        assert unmapped["retry_counters"] == {"repair": 2}


class TestMergeStateRoundTrip:
    """merge_state(blocks, unmapped=unmapped) 重建原 state dict（键集合 + 值）。"""

    def test_round_trip_preserves_key_set(self):
        original = {
            "session_id": "sess-1",
            "user_id": 42,
            "trace_id": "t-1",
            "memory_context": "ctx",
            "intent": "report",        # unmapped
            "user_query": "销售",       # unmapped
        }
        blocks, unmapped = split_state(original)
        merged = merge_state(blocks, unmapped=unmapped)
        assert set(merged.keys()) == set(original.keys())

    def test_round_trip_preserves_values(self):
        # review P1 #3 决议：split_state 是 v2 view 工具；输入用 v2 名
        # （migrate_checkpoint 已在 graph 入口把 active_sub_agent rename → active_agent）
        v2_state = {
            "session_id": "sess-1",
            "intent": "report",          # unmapped
            "active_agent": "execution",  # v2 名
        }
        blocks, unmapped = split_state(v2_state)
        merged = merge_state(blocks, unmapped=unmapped)
        assert merged["active_agent"] == "execution"
        assert merged["intent"] == "report"
        assert merged["session_id"] == "sess-1"

    def test_split_from_v1_shape_returns_v2_view(self):
        # 显式钉：v1 输入经 split → blocks 含 v2 名（rename 不丢名是预期）；
        # v1 源字段是 _MAPPED_SOURCE_FIELDS 成员，不进 unmapped
        v1_state = {"active_sub_agent": "execution"}
        blocks, unmapped = split_state(v1_state)
        assert blocks["runtime"]["active_agent"] == "execution"
        assert unmapped == {}

    def test_split_returns_blocks_with_five_names(self):
        blocks, _ = split_state({})
        assert set(blocks.keys()) == {
            "request", "requirement", "execution", "report", "runtime",
        }
