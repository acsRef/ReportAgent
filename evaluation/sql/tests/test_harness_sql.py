"""sql 子包 dispatcher 实装测试。"""
from __future__ import annotations

import pytest

from evaluation.checker import ObservedTurn
from evaluation.sql.harness import assert_sql


def test_sql_sql_nonempty_pass():
    obs = ObservedTurn(sql="SELECT 1")
    sec, _ = assert_sql(obs, {"sql_nonempty": True})
    assert sec["sql_nonempty"] == "pass"


def test_sql_sql_nonempty_fail():
    obs = ObservedTurn(sql="")
    sec, _ = assert_sql(obs, {"sql_nonempty": True})
    assert sec["sql_nonempty"] == "fail"


def test_sql_rows_gt_pass():
    obs = ObservedTurn(row_count=10)
    sec, _ = assert_sql(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "pass"


def test_sql_rows_gt_unknown():
    obs = ObservedTurn(row_count=None)
    sec, _ = assert_sql(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "fail"


def test_sql_verdict_success():
    obs = ObservedTurn(row_count=10, sql="SELECT 1")
    sec, _ = assert_sql(obs, {"verdict": "SUCCESS"})
    assert sec["verdict"] == "pass"


def test_sql_verdict_empty():
    obs = ObservedTurn(row_count=0, sql="SELECT 1")
    sec, _ = assert_sql(obs, {"verdict": "EMPTY"})
    assert sec["verdict"] == "pass"


def test_sql_verdict_failed():
    obs = ObservedTurn(sql="SELECT 1", error_code="SQL_SYNTAX_ERROR")
    sec, _ = assert_sql(obs, {"verdict": "FAILED"})
    assert sec["verdict"] == "pass"
