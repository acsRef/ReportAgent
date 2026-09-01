"""retrieval dim harness——P14b 阶段实装 Langfuse tool/observation 读 retrieval count。

D2 边界：与 memory 同——exp schema 已定义、observation 暂不可用 →
(`sections=[], deferred_keys=list(exp.keys())`)。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("retrieval")
def assert_retrieval(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。exp 形如 {"recalled": bool, "k_min": 1}。"""
    _ = obs
    return {}, list(exp.keys())
