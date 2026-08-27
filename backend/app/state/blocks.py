"""State 五块归位 contract 模块（P3 Task 1）。

P3 落地后本模块是 static ownership contract：
- 五块 TypedDict 字段名与 state-contract.md §一一字一致
- deterministic 映射表（review P1 #3：仅同名/同类型 rename，**不**做语义伪映射）
- split_state / merge_state = projection / compatibility utility
- 不是 runtime enforcement；单写者 enforcement 留给各 graph 后续 phase（P4 起）
"""
from __future__ import annotations

from typing import Optional, TypedDict


class RequestState(TypedDict, total=False):
    request_id: str
    session_id: str
    user_id: int
    original_query: str
    current_query: str


class RequirementState(TypedDict, total=False):
    normalized_query: str
    schema_candidates: list
    requirement_card: dict
    missing_dimensions: list
    clarification_history: list
    confirmation_status: str


class ExecutionState(TypedDict, total=False):
    confirmed_requirement: Optional[str]
    schema_context: Optional[dict]
    query_plan: Optional[dict]
    generated_sql: Optional[str]
    validation_result: Optional[dict]
    query_result: Optional[dict]
    execution_status: str
    error: Optional[dict]
    retry_count: int


class ReportState(TypedDict, total=False):
    report_spec: Optional[dict]
    report_version: Optional[int]
    chart_config: Optional[dict]
    insight: Optional[str]


class RuntimeState(TypedDict, total=False):
    trace_id: str
    active_agent: str
    memory_context: str
    tool_calls: list
    mcp_calls: list


# --- deterministic 映射表（review P1 #3 仅同名 / 同类型 rename）-------------
#
# 每条 (源字段名, 目标 block, 目标 v2 canonical 字段名)。
# v1 源字段经 rename 后归到 v2 目标字段（如 active_sub_agent → active_agent）；
# 一个 v2 目标可由多源映射（v1 rename 行 + v2 self 行）。

_DETERMINISTIC_MAPPING: tuple[tuple[str, str, str], ...] = (
    # RequestState
    ("request_id",            "request",     "request_id"),
    ("session_id",            "request",     "session_id"),
    ("user_id",               "request",     "user_id"),
    ("original_query",        "request",     "original_query"),
    ("current_query",         "request",     "current_query"),
    # RequirementState
    ("normalized_query",      "requirement", "normalized_query"),
    ("schema_candidates",     "requirement", "schema_candidates"),
    ("requirement_card",      "requirement", "requirement_card"),
    ("missing_dimensions",    "requirement", "missing_dimensions"),
    ("clarification_history", "requirement", "clarification_history"),
    ("confirmation_status",   "requirement", "confirmation_status"),
    # ExecutionState
    ("confirmed_requirement", "execution",   "confirmed_requirement"),
    ("schema_context",        "execution",   "schema_context"),
    ("query_plan",            "execution",   "query_plan"),
    ("generated_sql",         "execution",   "generated_sql"),
    ("validation_result",     "execution",   "validation_result"),
    ("query_result",          "execution",   "query_result"),
    ("execution_status",      "execution",   "execution_status"),
    ("error",                 "execution",   "error"),
    ("retry_count",           "execution",   "retry_count"),
    # ReportState（含 v1→v2 rename: insight_text → insight）
    ("report_spec",           "report",      "report_spec"),
    ("report_version",        "report",      "report_version"),
    ("chart_config",          "report",      "chart_config"),
    ("insight_text",          "report",      "insight"),
    ("insight",               "report",      "insight"),
    # RuntimeState（含 v1→v2 rename: active_sub_agent → active_agent）
    ("trace_id",              "runtime",     "trace_id"),
    ("active_sub_agent",      "runtime",     "active_agent"),
    ("active_agent",          "runtime",     "active_agent"),
    ("memory_context",        "runtime",     "memory_context"),
    ("tool_calls",            "runtime",     "tool_calls"),
    ("mcp_calls",             "runtime",     "mcp_calls"),
)

_MAPPED_SOURCE_FIELDS: frozenset[str] = frozenset(
    src for src, _, _ in _DETERMINISTIC_MAPPING
)


def split_state(state_dict: dict) -> tuple[dict, dict]:
    """按 deterministic 映射表投影到五块子 dict；未映射字段进 unmapped。

    输入既可是 v1 shape 也可是 v2 shape。映射表覆盖两代源字段名。v1 源字段
    （active_sub_agent / insight_text）经 rename 后归到 v2 目标字段；源字段名
    不进 unmapped（已被 split 消费）。
    """
    blocks: dict = {
        "request": {},
        "requirement": {},
        "execution": {},
        "report": {},
        "runtime": {},
    }
    unmapped: dict = {}

    for src, block_name, dst_field in _DETERMINISTIC_MAPPING:
        if src in state_dict:
            blocks[block_name][dst_field] = state_dict[src]

    for key, value in state_dict.items():
        if key in _MAPPED_SOURCE_FIELDS:
            continue
        unmapped[key] = value

    return blocks, unmapped


def merge_state(blocks: dict, *, unmapped: dict | None = None) -> dict:
    """合并回 state dict（blocks 优先填，unmapped 补充未归类字段）。

    与 split_state 配合：对 v2 shape 输入，merge_state(*split_state(s)) 的键集合
    与值应与 s 一致。v1 shape 输入经 split 后 merged 是 v2 形态（rename 不丢名是预期）。
    """
    merged: dict = {}
    for block in blocks.values():
        for key, value in block.items():
            merged[key] = value
    if unmapped:
        merged.update(unmapped)
    return merged
