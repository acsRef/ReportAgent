"""memory dim harness——P14b 阶段实装 Langfuse trace 查询。

P14 阶段（骨架）：dispatcher hook + 真正 deferred 占位。
D2 边界：exp schema 已定义（recalled / types_any_of），但 observation 暂不可用——
返回 `(sections=[], deferred_keys=list(exp.keys()))`，让 dim_results[memory] 的
`deferred` 字段等于期望 key 数，用户能区分「schema 未定义」（frontend/e2e）vs
「schema 已定但 observation 未接入」（memory/retrieval/tool_selection/repair）。

P14b 阶段：扩 ObservedTurn.langfuse_trace + 接入 backend/app/observability/langfuse_flush.py
读 memory_recall_observed / memory_types_observed，比对 exp，sections 替换 deferred。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("memory")
def assert_memory(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。

    exp 形如：
      {"recalled": bool, "types_any_of": ["conversation", "session"]}

    返回：(sections=[], deferred_keys=每个 exp key)
    """
    _ = obs
    return {}, list(exp.keys())
