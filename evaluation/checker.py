"""判定引擎：ObservedTurn（runner 组装的观测）vs TurnExpectation（数据集期望）。

纯函数，不碰网络。原则（冻结基线 §十四）：
- 可观测即判定；不可观测即 deferred（不影响 pass/fail）。
- verdict 推导对齐三态语义 SUCCESS / EMPTY / FAILED。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ObservedTurn(BaseModel):
    """runner 从 SSE 事件 + GET /sessions/{sid}/reports/{v} 快照组装的一轮观测。"""

    sse_events: list[str] = Field(default_factory=list)
    card_status: str | None = None
    missing_fields_count: int | None = None
    target_metrics: list[str] = Field(default_factory=list)
    time_range: str | None = None
    scope: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    sql: str | None = None
    row_count: int | None = None
    error_code: str | None = None
    table_present: bool = False
    chart_present: bool = False
    table_rows: int | None = None
    latency_ms: float | None = None


def _derive_verdict(obs: ObservedTurn) -> str:
    """对齐 report_version_service 三态语义。"""
    if obs.error_code:
        return "FAILED"
    if obs.row_count == 0:
        return "EMPTY"
    if obs.row_count and obs.row_count > 0:
        return "SUCCESS"
    return "UNKNOWN"  # 无 error 也无 row_count 观测 → 本轮未执行或未取到快照


# (observed_value, expected_value) → section 名
def check_turn(
    observed: ObservedTurn, expectation: dict[str, Any]
) -> tuple[dict[str, str], list[str]]:
    """返回 ({section: pass|fail}, deferred_keys)。任何 fail 即该例 fail。"""
    sections: dict[str, str] = {}
    deferred: list[str] = []
    exp = expectation or {}

    # ---- requirement ----
    req = exp.get("requirement") or {}
    if req.get("status") is not None:
        sections["requirement.status"] = (
            "pass" if observed.card_status == req["status"] else "fail"
        )
    if req.get("min_missing_fields") is not None:
        got = observed.missing_fields_count
        sections["requirement.min_missing_fields"] = (
            "pass" if got is not None and got >= req["min_missing_fields"] else "fail"
        )
    if req.get("time_range_equals") is not None:
        sections["requirement.time_range_equals"] = (
            "pass" if observed.time_range == req["time_range_equals"] else "fail"
        )
    if req.get("target_metrics_contains"):
        want_any = req["target_metrics_contains"]
        hit = any(any(w in m for m in observed.target_metrics) for w in want_any)
        sections["requirement.target_metrics"] = "pass" if hit else "fail"

    # ---- execution ----
    exe = exp.get("execution") or {}
    if exe.get("verdict") is not None:
        derived = _derive_verdict(observed)
        sections["execution.verdict"] = (
            "pass" if derived == exe["verdict"] else
            f"fail(derived={derived})"
        ) if derived != exe["verdict"] else "pass"
    if exe.get("sql_nonempty"):
        sections["execution.sql_nonempty"] = (
            "pass" if bool(observed.sql and observed.sql.strip()) else "fail"
        )
    if exe.get("rows_gt") is not None:
        rc = observed.row_count
        sections["execution.rows_gt"] = (
            "pass" if rc is not None and rc > exe["rows_gt"] else "fail"
        )
    if exe.get("sse_error_code"):
        sections["execution.sse_error_code"] = (
            "pass" if observed.error_code == exe["sse_error_code"] else "fail"
        )

    # ---- report ----
    rep = exp.get("report") or {}
    if rep.get("table_present") is not None:
        sections["report.table_present"] = (
            "pass" if observed.table_present == rep["table_present"] else "fail"
        )
    if rep.get("chart_present") is not None:
        sections["report.chart_present"] = (
            "pass" if observed.chart_present == rep["chart_present"] else "fail"
        )
    if rep.get("rows_gt") is not None:
        tr = observed.table_rows
        sections["report.rows_gt"] = (
            "pass" if tr is not None and tr > rep["rows_gt"] else "fail"
        )

    # ---- behavior ----
    beh = exp.get("behavior") or {}
    if beh.get("clarification") is not None:
        # 可观测：card_status 是否 missing。
        obs_clarify = observed.card_status == "missing"
        sections["behavior.clarification"] = (
            "pass" if obs_clarify == beh["clarification"] else "fail"
        )
    for key in ("memory_required", "memory_types", "retrieval"):
        if beh.get(key) is not None:
            deferred.append(f"behavior.{key}")  # 内部观测 → P13 Langfuse 前不判定

    return sections, deferred


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合口径：skip/error 不计入分母的比率 + latency 分位。"""
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "pass")
    failed = sum(1 for r in results if r.get("status") == "fail")
    skipped = sum(
        1 for r in results if r.get("status") in ("skip", "error")
    )

    executed = [r for r in results if r.get("sql_executed")]
    sql_ok = sum(1 for r in executed if r.get("status") == "pass")
    latencies = sorted(
        r["latency_ms"] for r in results
        if r.get("latency_ms") is not None
    )

    def _pct(p: float) -> float | None:
        if not latencies:
            return None
        idx = min(len(latencies), max(1, round(p * len(latencies))))
        return latencies[idx - 1]

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped_or_error": skipped,
        "sql_success_rate": (sql_ok / len(executed)) if executed else None,
        "p50_latency_ms": _pct(0.50),
        "p95_latency_ms": _pct(0.95),
    }
