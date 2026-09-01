"""e2e dim 真 no-op 占位（P12 Playwright 已 done；evaluation e2e = 现有 runner）。

D2 边界：与 frontend 同——无 expectation schema → 不计 deferred。
未来填点（建议但不实施）：Playwright Full E2E 启动延迟 / report 渲染一致性 /
session resume round-trip 等。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("e2e")
def assert_e2e(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """真 no-op：不在 dim_results 体现 deferred。"""
    _ = obs
    _ = exp
    return {}, []
