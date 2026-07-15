from __future__ import annotations

from typing import Any, TypedDict


class TraceStep(TypedDict):
    step: str
    status: str
    detail: str
    duration: str


class AgentState(TypedDict):
    messages: list
    user_query: str
    session_id: str
    intent: str
    memory_context: str
    schema_context: str
    generated_sql: str
    sql_valid: bool
    sql_result: str
    sql_error: str
    retry_count: int
    need_clarification: bool
    clarification_question: str
    clarification_answer: str
    chart_config: dict
    insight_text: str
    trace_log: list[TraceStep]
    assemble_plan: list[dict]
    assemble_step_idx: int
    assemble_results: list[dict]
    error: str
