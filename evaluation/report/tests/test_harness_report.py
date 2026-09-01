"""report 子包 dispatcher 实装测试。

注：report 在 LEGACY_KEYS 内，本子包函数 Phase 2 dispatcher 不调用（兼容注册位）。
测试直接调 assert_report() 函数体，不验证 dispatcher 行为。
"""
from __future__ import annotations

import pytest

from evaluation.checker import ObservedTurn
from evaluation.report.harness import assert_report


def test_report_table_present_pass():
    obs = ObservedTurn(table_present=True)
    sec, _ = assert_report(obs, {"table_present": True})
    assert sec["table_present"] == "pass"


def test_report_table_present_fail():
    obs = ObservedTurn(table_present=False)
    sec, _ = assert_report(obs, {"table_present": True})
    assert sec["table_present"] == "fail"


def test_report_chart_present_pass():
    obs = ObservedTurn(chart_present=True)
    sec, _ = assert_report(obs, {"chart_present": True})
    assert sec["chart_present"] == "pass"


def test_report_rows_gt_pass():
    obs = ObservedTurn(table_rows=10)
    sec, _ = assert_report(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "pass"


def test_report_rows_gt_unknown():
    obs = ObservedTurn(table_rows=None)
    sec, _ = assert_report(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "fail"
