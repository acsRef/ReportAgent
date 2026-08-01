"""P-8: extract_sql 多语句截断测试。

只取第一条语句——修 sqlglot.parse_one 多语句解析失败，同时截掉
`SELECT…; DELETE…` 这类注入尾部，并顺带去掉尾随分号。
"""
from __future__ import annotations

import pytest

from app.utils.text import extract_sql

pytestmark = pytest.mark.smoke


def test_single_statement_unchanged():
    assert extract_sql("SELECT * FROM fact_sales") == "SELECT * FROM fact_sales"


def test_strips_trailing_semicolon():
    assert extract_sql("SELECT * FROM fact_sales;") == "SELECT * FROM fact_sales"


def test_takes_first_of_multi_statement():
    assert extract_sql("SELECT 1; SELECT 2") == "SELECT 1"


def test_drops_injection_tail():
    # 注入的第二条语句被直接丢弃，绝不透传进安检链路
    assert extract_sql("SELECT region FROM fact_sales; DELETE FROM fact_sales") == \
        "SELECT region FROM fact_sales"


def test_leading_non_sql_stripped_then_first_statement():
    assert extract_sql("好的，SQL 如下：SELECT a FROM t; SELECT b FROM t2") == "SELECT a FROM t"


def test_no_select_returns_empty():
    assert extract_sql("这里没有查询") == ""


def test_empty_returns_empty():
    assert extract_sql("") == ""


def test_think_block_still_stripped_with_multi_statement():
    assert extract_sql("<think>推理</think>SELECT x FROM t; DROP TABLE t") == "SELECT x FROM t"
