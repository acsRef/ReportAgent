"""T2: checker 纯函数测试 —— baseline-lock plan T2。

ObservedTurn 由 runner 从 SSE 事件 + 报告快照组装；check_turn 是纯函数，
离线可测。verdict 推导对齐三态语义：error → FAILED；rows==0 且无 error →
EMPTY；rows>0 → SUCCESS。
"""
from __future__ import annotations

import pytest

from evaluation.checker import (
    ObservedTurn,
    check_turn,
    summarize,
)


def _obs(**kw) -> ObservedTurn:
    base = dict(
        sse_events=["phase", "requirement", "done"],
        card_status="complete",
        missing_fields_count=0,
        target_metrics=["销售额"],
        time_range="2024年",
        scope=[],
        dimensions=[],
        sql="SELECT 1",
        row_count=5,
        error_code=None,
        table_present=True,
        chart_present=False,
        table_rows=5,
    )
    base.update(kw)
    return ObservedTurn(**base)


class TestCheckTurn:
    def test_success_case_passes(self):
        exp = {"execution": {"verdict": "SUCCESS", "sql_nonempty": True},
               "report": {"table_present": True, "rows_gt": 0}}
        sections, deferred = check_turn(_obs(), exp)
        assert all(v == "pass" for v in sections.values()), sections
        assert deferred == []

    def test_empty_verdict_passes(self):
        obs = _obs(row_count=0, table_present=False, table_rows=0, sql="SELECT 1")
        exp = {"execution": {"verdict": "EMPTY", "sql_nonempty": True}}
        sections, _ = check_turn(obs, exp)
        assert sections["execution.verdict"] == "pass"

    def test_expected_success_but_error_fails(self):
        obs = _obs(error_code="QUERY_TIMEOUT", row_count=None)
        exp = {"execution": {"verdict": "SUCCESS"}}
        sections, _ = check_turn(obs, exp)
        # fail 值带 derived 诊断信息。
        assert sections["execution.verdict"].startswith("fail")

    def test_sse_error_code_expectation(self):
        obs = _obs(error_code="SECURITY_REJECTED")
        exp = {"execution": {"sse_error_code": "SECURITY_REJECTED"}}
        sections, _ = check_turn(obs, exp)
        assert sections["execution.sse_error_code"] == "pass"

    def test_requirement_fields_checked(self):
        exp = {
            "requirement": {
                "status": "missing",
                "min_missing_fields": 1,
                "time_range_equals": None,
            }
        }
        obs = _obs(card_status="missing", missing_fields_count=2)
        sections, _ = check_turn(obs, exp)
        assert sections["requirement.status"] == "pass"
        assert sections["requirement.min_missing_fields"] == "pass"

    def test_time_range_mismatch_fails(self):
        exp = {"requirement": {"time_range_equals": "2023年"}}
        sections, _ = check_turn(_obs(time_range="2024年"), exp)
        assert sections["requirement.time_range_equals"] == "fail"

    def test_memory_expectation_is_deferred_not_fail(self):
        exp = {"behavior": {"memory_required": True,
                            "memory_types": ["conversation"]}}
        sections, deferred = check_turn(_obs(), exp)
        # 不产生任何 fail；进 deferred。
        assert all(v == "pass" for v in sections.values())
        assert any("memory" in d for d in deferred)

    def test_clarification_observable(self):
        exp = {"behavior": {"clarification": False}}
        sections, _ = check_turn(_obs(), exp)
        assert sections.get("behavior.clarification") in ("pass",)

    def test_empty_expectation_no_sections(self):
        sections, deferred = check_turn(_obs(), {})
        assert sections == {} and deferred == []


class TestSummarize:
    def test_rates_math(self):
        results = [
            {"status": "pass"},
            {"status": "pass"},
            {"status": "fail"},
            {"status": "skip"},
        ]
        s = summarize(results)
        assert s["total"] == 4
        assert s["passed"] == 2 and s["failed"] == 1

    def test_empty_input_no_division_error(self):
        s = summarize([])
        assert s["total"] == 0 and s["passed"] == 0

    def test_sql_rate_excludes_skipped(self):
        results = [
            {"status": "pass", "sql_executed": True},
            {"status": "fail", "sql_executed": True},
            {"status": "skip", "sql_executed": False},
        ]
        s = summarize(results)
        assert abs(s["sql_success_rate"] - 0.5) < 1e-9

    def test_latencies_percentiles(self):
        results = [
            {"status": "pass", "latency_ms": float(x)} for x in range(1, 101)
        ]
        s = summarize(results)
        # nearest-rank：p50 = 第 ceil(0.5*100)=50 个值 = 50.0
        assert s["p50_latency_ms"] == pytest.approx(50.0)
        assert s["p95_latency_ms"] == pytest.approx(95.0)
