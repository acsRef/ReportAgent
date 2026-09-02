"""evaluation.checker.build_dim_results 纯函数行为测试。

设计要求：不依赖 runner.run_case，直接 unit test 一个纯函数。
"""
from __future__ import annotations

import pytest

from evaluation.checker import build_dim_results


def test_build_dim_results_pass_section_counts():
    """纯函数：section 写 pass → 对应 dim 的 pass 计数增加。"""
    sections = {
        "sql.sql_nonempty": "pass",
        "sql.rows_gt": "pass",
    }
    out = build_dim_results(sections, deferred=[], dims=["sql"])
    assert out["sql"] == {"pass": 2, "fail": 0, "deferred": 0}


def test_build_dim_results_fail_section_startswith_fail():
    """fail 类 section 形式包含 'fail' 前缀（e.g. 'fail(derived=EMPTY)'）也算 fail。"""
    sections = {"sql.rows_gt": "fail(derived=EMPTY)"}
    out = build_dim_results(sections, deferred=[], dims=["sql"])
    assert out["sql"]["fail"] == 1
    assert out["sql"]["pass"] == 0


def test_build_dim_results_deferred_keys_counted():
    """deferred list 包含 dim 前缀的 key → dim 的 deferred 计数增加。"""
    sections = {"report.table_present": "pass"}
    deferred = ["memory.recalled", "memory.types_any_of"]
    out = build_dim_results(sections, deferred, dims=["report", "memory"])
    assert out["report"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["memory"] == {"pass": 0, "fail": 0, "deferred": 2}


def test_build_dim_results_dim_unknown_returns_zero():
    """未出现的 dim 返回 0/0/0 默认值。"""
    out = build_dim_results({}, [], dims=["sql", "memory"])
    assert out["sql"] == {"pass": 0, "fail": 0, "deferred": 0}
    assert out["memory"] == {"pass": 0, "fail": 0, "deferred": 0}


def test_build_dim_results_multiple_dims_separated():
    """多 dim sections 各自归属（不串）——用 `dim.` 前缀边界而非 `dim` contains。"""
    sections = {
        "sql.sql_nonempty": "pass",
        "sqltable.rows_gt": "pass",  # 不应被 sql.* 误归
    }
    out = build_dim_results(sections, [], dims=["sql"])
    # 'sqltable.' 与 'sql.' 是不同 prefix（前缀边界 = '.'）
    assert out["sql"] == {"pass": 1, "fail": 0, "deferred": 0}


def test_build_dim_results_calls_with_legacy_dims():
    """runner 调用 build_dim_results 时 dims 包含 legacy 4 段+registry 9 段共 11 维度；纯函数不关心。"""
    sections = {
        "requirement.status": "pass",
        "execution.verdict": "pass",
        "report.table_present": "pass",
        "behavior.clarification": "pass",
    }
    out = build_dim_results(sections, [], dims=["requirement", "execution", "report", "behavior"])
    assert out["requirement"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["execution"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["report"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["behavior"] == {"pass": 1, "fail": 0, "deferred": 0}
