"""T3: 数据集体检测试 —— 全部 case 过 schema、id 唯一、category 白名单、
fault-injection 占位数正确。"""
from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.loader import load_all
from evaluation.schema import BaselineCase

DATASET = Path(__file__).resolve().parents[1] / "baseline_cases.json"

EXPECTED_CATEGORIES = {
    "explicit_query", "clarification", "multi_turn", "complex_sql",
    "security", "sql_failure", "empty", "report_chart",
    "memory_preference", "chitchat", "schema_retrieval",
    "mcp_failure", "sql_repair",
}


@pytest.fixture(scope="module")
def cases() -> list[BaselineCase]:
    return load_all(DATASET)


def test_dataset_loads(cases):
    assert len(cases) == 20


def test_ids_unique_and_kebab(cases):
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    import re
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in ids)


def test_categories_in_whitelist(cases):
    assert {c.category for c in cases} <= EXPECTED_CATEGORIES


def test_fault_injection_count(cases):
    fi = [c.id for c in cases if c.requires_fault_injection]
    assert sorted(fi) == [
        "mcp-down-requirement-scope",
        "mcp-failure-unavailable",
        "sql-failure-fault-injection",
        "sql-repair-recovers",
    ]


def test_fault_cases_declare_injection_spec(cases):
    """故障用例必须带 `fault` 规格 —— 否则 runner 只能 skip，报告里像“跑过了”。

    （2026-11-02：此前 4 例都是占位，runner 见 flag 直接 skip，错误路径零覆盖。）
    """
    for c in cases:
        if c.requires_fault_injection:
            assert c.fault is not None, f"{c.id}: 只标了 requires_fault_injection 却没给 fault 规格"
            assert c.fault.header(), f"{c.id}: fault 规格产不出请求头"


def test_fault_spec_header_shapes(cases):
    by_id = {c.id: c for c in cases}
    assert by_id["sql-failure-fault-injection"].fault.header() == {
        "X-E2E-Fault": "kind=permission;mode=persistent",
    }
    assert by_id["sql-repair-recovers"].fault.header() == {
        "X-E2E-Fault": "kind=object_not_found;mode=once",
    }
    mcp = by_id["mcp-failure-unavailable"].fault
    assert mcp.header() == {"X-E2E-McpDown": "on"}
    # on=both 两个请求都带头；on=chat 时 confirm 不带头
    assert mcp.headers_for("chat") == {"X-E2E-McpDown": "on"}
    assert mcp.headers_for("confirm") == {"X-E2E-McpDown": "on"}
    chat_only = by_id["mcp-down-requirement-scope"].fault
    assert chat_only.headers_for("chat") == {"X-E2E-McpDown": "on"}
    assert chat_only.headers_for("confirm") == {}


def test_every_case_has_nonempty_expectation_content(cases):
    """每例至少一个 expectation 段非空（空 {} 不算有期望）。"""
    for c in cases:
        assert any(
            any(v is not None and v != [] for v in e.model_dump().values())
            for e in c.expectations
        ), f"{c.id}: expectations 全空"


def test_seed_time_window_respected(cases):
    """除 EMPTY 例（2025）外，query 中不得出现 2025+ 年份。

    另：seed 的 dim_date 实际只覆盖 2024 全年（CLAUDE.md 写的 2020~2024
    是过时描述），非 EMPTY 案例统一用 2024。
    """
    for c in cases:
        if c.category == "empty":
            continue
        for t in c.turns:
            for y in ("2025", "2026"):
                assert y not in t.query, f"{c.id} 用了超种子范围的年份: {t.query}"
            assert "2023" not in t.query, (
                f"{c.id} 用了 2023 —— seed 只有 2024 数据（dim_date 仅 2024 全年）"
            )


def test_multiturn_cases_have_aligned_expectations(cases):
    for c in cases:
        if len(c.turns) > 1:
            assert len(c.expectations) in (1, len(c.turns))
