"""判定引擎：ObservedTurn（runner 组装的观测）vs TurnExpectation（数据集期望）。

纯函数，不碰网络。原则（冻结基线 §十四）：
- 可观测即判定；不可观测即 deferred（不影响 pass/fail）。
- verdict 推导对齐三态语义 SUCCESS / EMPTY / FAILED。
- P14 升级：Phase 1（legacy 4 段）+ Phase 2（9 子包 dispatcher）。
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field


# P14 dispatcher registry（key = dim 名, value = (obs, exp) -> (sections, deferred_keys)）
DIM_REGISTRY: dict[str, Callable[["ObservedTurn", dict], tuple[dict[str, str], list[str]]]] = {}


# D1 边界：LEGACY_KEYS 内 4 个 dim 由 Phase 1 legacy 唯一负责；Phase 2 dispatcher 跳过。
# requirement / execution / report / behavior 是 legacy 来源；execution / behavior 永不在
# DIM_REGISTRY，requirement / report 注册但被本常量跳过（兼容注册位）。
LEGACY_KEYS: frozenset[str] = frozenset({"requirement", "execution", "report", "behavior"})


def register_dim(name: str) -> Callable:
    """子包 harness 注册装饰器（幂等：重复 import 不覆盖）。"""
    def deco(fn: Callable) -> Callable:
        if name not in DIM_REGISTRY:
            DIM_REGISTRY[name] = fn
        return fn
    return deco


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
    """返回 ({section: pass|fail}, deferred_keys)。任何 fail 即该例 fail。

    Phase 1（legacy）：requirement / execution / report / behavior 4 段（P0-P12 行为冻结）。
    Phase 2（dispatcher）：遍历 DIM_REGISTRY 9 个 dim，跳过 LEGACY_KEYS，
      对其余 7 dim（memory / retrieval / tool_selection / sql / repair / frontend / e2e）调 harness。
    """
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

    # ---- Phase 2: 9 子包 dispatcher（D1 边界：LEGACY_KEYS 内 dim 跳过）----
    for dim, fn in DIM_REGISTRY.items():
        if dim in LEGACY_KEYS:
            continue
        if dim in exp and isinstance(exp[dim], dict):
            dim_sections, dim_deferred = fn(observed, exp[dim])
            sections.update({f"{dim}.{k}": v for k, v in dim_sections.items()})
            deferred.extend([f"{dim}.{k}" for k in dim_deferred])

    return sections, deferred


def build_dim_results(
    sections: dict[str, str],
    deferred: list[str],
    dims: "list[str] | tuple[str, ...] | set[str]",
) -> dict[str, dict[str, int]]:
    """聚合各 dim 的 pass/fail/deferred 数量。

    纯函数，**不**依赖 runner / DIM_REGISTRY / 测试依赖。
    输入：
      - sections: 已合并的 section dict（legacy + dispatcher 都贡献 key）
      - deferred: 已合并的 deferred key 列表
      - dims: 要聚合的 dim 列表（runner 调时 = registry 9 + legacy 4 = 11，含 {requirement, report} 重叠）
    输出：
      {dim: {"pass": int, "fail": int, "deferred": int}}

    dim 归属规则：`k.startswith(f"{dim}.")`（前缀 + dot 是边界），
    避免 `dim` contains 误把 `sqltable.foo` 归到 `sql`。
    """
    out: dict[str, dict[str, int]] = {}
    for dim in dims:
        prefix = f"{dim}."
        keys_pass = sum(1 for k, v in sections.items() if k.startswith(prefix) and v == "pass")
        keys_fail = sum(
            1 for k, v in sections.items() if k.startswith(prefix) and v.startswith("fail")
        )
        keys_deferred = sum(1 for k in deferred if k.startswith(prefix))
        out[dim] = {"pass": keys_pass, "fail": keys_fail, "deferred": keys_deferred}
    return out


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
