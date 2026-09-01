"""requirement dim harness——RequirementCard 字段级判定实装。

复用 evaluation/checker.py legacy requirement 段逻辑，封装为子包函数。
section key 不带 prefix：status / min_missing_fields / time_range_equals / target_metrics。
dispatcher 调时自动加 `requirement.` prefix（与 legacy 段 section key 不冲突，因为
LEGACY_KEYS 跳过 requirement，Phase 2 dispatcher 不会跑此函数——保留作为
「兼容注册位」让未来统一 9-dim dispatcher 重构无需改 __init__.py）。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("requirement")
def assert_requirement(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    deferred: list[str] = []
    if exp.get("status") is not None:
        sections["status"] = "pass" if obs.card_status == exp["status"] else "fail"
    if exp.get("min_missing_fields") is not None:
        got = obs.missing_fields_count
        sections["min_missing_fields"] = (
            "pass" if got is not None and got >= exp["min_missing_fields"] else "fail"
        )
    if exp.get("time_range_equals") is not None:
        sections["time_range_equals"] = (
            "pass" if obs.time_range == exp["time_range_equals"] else "fail"
        )
    if exp.get("target_metrics_contains"):
        want_any = exp["target_metrics_contains"]
        hit = any(any(w in m for m in obs.target_metrics) for w in want_any)
        sections["target_metrics"] = "pass" if hit else "fail"
    return sections, deferred
