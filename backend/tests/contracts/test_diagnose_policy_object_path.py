"""DiagnosePolicy 对 object_not_found / object_ambiguous 的修复路径。

P15 prelude fix（方案 A，用户 2026-09-02 拍板）：
- object_not_found → retry_mcp_schema_retrieval → escalate clarify
- object_ambiguous → 直接 clarify（用户必须消歧列名）
- 旧 'object' kind（向后兼容未识别 ProgrammingError 兜底）→ 仍 retry_sql

counter key "mcp_schema" 与 "sql_generation" / "plan" 三 key 正交，独立预算。
DiagnoseDecision.retry_target Literal 扩展加 "mcp_schema"，
DiagnoseDecision.action Literal 扩展加 "retry_mcp_schema_retrieval"。
"""
from __future__ import annotations

from app.agent.sql_graph import DiagnosePolicy


def test_object_not_found_triggers_mcp_schema_retrieval():
    """object_not_found 错 + 未调过 schema retrieval → retry_mcp_schema_retrieval。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "retry_mcp_schema_retrieval"
    assert dec.recoverable is True
    assert dec.retry_target == "mcp_schema"  # 触发 MCP schema retrieval


def test_object_not_found_after_schema_retrieval_escalates_clarify():
    """object_not_found 错 + 已调过 schema retrieval → escalate clarify（避免死循环）。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        retry_counters={"sql_generation": 0, "plan": 0, "mcp_schema": 1},  # 已调 1 次
    )
    assert dec.action == "clarify"
    assert dec.recoverable is False


def test_object_ambiguous_goes_straight_to_clarify():
    """AmbiguousColumn 类错（列名歧义）→ 必须用户消歧，直接 clarify。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_ambiguous",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "clarify"
    assert dec.recoverable is False


def test_object_legacy_kind_keeps_old_retry_sql_behavior():
    """向后兼容：旧 'object' kind（来自未识别 ProgrammingError）→ 仍 retry_sql。"""
    dec = DiagnosePolicy.decide(
        error_kind="object",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "retry_sql"