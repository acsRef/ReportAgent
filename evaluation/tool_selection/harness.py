"""tool_selection dim harness——P14b 阶段读 Langfuse tool_call span list；

P14 阶段 deferred 占位（D2 边界）。P14b 实装：从 obs.langfuse_trace 抽取 tool name 列表比对 exp。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("tool_selection")
def assert_tool_selection(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。"""
    _ = obs
    return {}, list(exp.keys())
