"""report dim harness——ReportSpec 字段溯源判定。

复用 evaluation/checker.py legacy report 段逻辑，但 focus 在
P10 三层 Validator 感兴趣的字段（KPI / Table 字段 / Chart type）。
本子包与 legacy report 段两套并存（dispatcher 通过 LEGACY_KEYS 跳过，
所以不会被 Phase 2 重写；保留作为兼容注册位）。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("report")
def assert_report(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    deferred: list[str] = []
    if exp.get("table_present") is not None:
        sections["table_present"] = (
            "pass" if obs.table_present == exp["table_present"] else "fail"
        )
    if exp.get("chart_present") is not None:
        sections["chart_present"] = (
            "pass" if obs.chart_present == exp["chart_present"] else "fail"
        )
    if exp.get("rows_gt") is not None:
        tr = obs.table_rows
        sections["rows_gt"] = "pass" if tr is not None and tr > exp["rows_gt"] else "fail"
    return sections, deferred
