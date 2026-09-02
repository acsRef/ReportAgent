"""frontend dim 真 no-op 占位（P11 已落地前端契约冻结；本 plan 不重测）。

D2 边界：frontend 子包无 expectation schema——属于「未定义期望」而非「延后判定」，
因此返回 `(sections=[], deferred_keys=[])`（不是 `list(exp.keys())`）。
dim_results[frontend] = {pass:0, fail:0, deferred:0} 是预期状态，不是缺漏。

未来填点（建议但不实施）：phase 状态机迁移正确性 / EventSource reconnect 行为 /
ProgressCard 真 trace 驱动 fallback 等。本子包作为「前端 P14 评估」hook 占位。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("frontend")
def assert_frontend(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """真 no-op：不在 dim_results 体现 deferred。"""
    _ = obs
    _ = exp
    return {}, []
