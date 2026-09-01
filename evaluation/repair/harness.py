"""repair dim harness——P14b 阶段读 Langfuse 中 sql.repair span 比对 retry_count。

D2 边界：与 memory 同——exp schema 已定义、observation 暂不可用 →
`(sections=[], deferred_keys=list(exp.keys())`)。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("repair")
def assert_repair(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。exp 形如 {"used": bool, "retries_max": 2, "succeeded_within_budget": bool}。"""
    _ = obs
    return {}, list(exp.keys())
