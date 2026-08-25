"""T4: runner 离线部分测试 —— summarize → Markdown 渲染（不碰网络）。"""
from __future__ import annotations

from evaluation.runner import render_markdown

FAKE_RESULTS = [
    {
        "case_id": "explicit-region-sales-ranking",
        "category": "explicit_query",
        "status": "pass",
        "sections": {"execution.verdict": "pass", "report.table_present": "pass"},
        "deferred": [],
        "sql_executed": True,
        "latency_ms": 1200.0,
    },
    {
        "case_id": "clarify-vague-metric",
        "category": "clarification",
        "status": "fail",
        "sections": {"requirement.status": "fail(got=complete)"},
        "deferred": ["behavior.memory_required"],
        "sql_executed": False,
        "latency_ms": 800.0,
    },
    {
        "case_id": "mcp-failure-timeout",
        "category": "mcp_failure",
        "status": "skip",
        "reason": "requires_fault_injection",
        "sections": {},
        "deferred": [],
        "sql_executed": False,
        "latency_ms": None,
    },
]


def test_renders_case_rows():
    md = render_markdown(FAKE_RESULTS, summary=None)
    assert "| explicit-region-sales-ranking |" in md
    assert "| clarify-vague-metric |" in md
    assert "skip" in md


def test_renders_fail_detail():
    md = render_markdown(FAKE_RESULTS, summary=None)
    assert "requirement.status" in md  # fail 明细可见


def test_renders_summary_block():
    summary = {
        "total": 3, "passed": 1, "failed": 1, "skipped_or_error": 1,
        "sql_success_rate": 1.0, "p50_latency_ms": 800.0, "p95_latency_ms": 1200.0,
    }
    md = render_markdown(FAKE_RESULTS, summary=summary)
    assert "passed" in md and "failed" in md
    assert "p50" in md.lower() and "p95" in md.lower()


def test_empty_results_no_crash():
    md = render_markdown([], summary={"total": 0})
    assert isinstance(md, str)
