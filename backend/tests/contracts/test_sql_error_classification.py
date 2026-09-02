"""psycopg2.errors 子类到 SQL ErrorKind 的精确分类。

P15 prelude fix：原 _classify_psycopg2_error 把所有非权限/语法的 ProgrammingError
归 'object'，过粗——拼写错的列名 / 表名 / 函数名被无信息增益地反复 retry，
烧光 MAX_SQL_REPAIR_RETRIES 预算。本测试钉精确子类映射，让 DiagnosePolicy
有机会在 object_not_found 路径走 retry_mcp_schema_retrieval。
"""
from __future__ import annotations

import pytest

import psycopg2.errors

from app.tools.sql_tools import _classify_psycopg2_error


@pytest.mark.parametrize("exc_cls,expected_kind", [
    # 既有边界（不破）
    (psycopg2.errors.QueryCanceled, "timeout"),
    (psycopg2.errors.AdminShutdown, "timeout"),
    (psycopg2.errors.CrashShutdown, "timeout"),
    (psycopg2.OperationalError, "connection"),
    (psycopg2.errors.SyntaxError, "syntax"),
    # P15 新增精确子类
    (psycopg2.errors.UndefinedColumn, "object_not_found"),
    (psycopg2.errors.UndefinedTable, "object_not_found"),
    (psycopg2.errors.UndefinedFunction, "object_not_found"),
    (psycopg2.errors.AmbiguousColumn, "object_ambiguous"),
    (psycopg2.errors.DivisionByZero, "other"),
    (psycopg2.errors.DatatypeMismatch, "other"),
])
def test_classify_specific_subclasses(exc_cls, expected_kind):
    """精确子类必须映射到对应 kind，不退到 ProgrammingError 兜底。"""
    assert _classify_psycopg2_error(exc_cls("simulated")) == expected_kind


def test_classify_programming_error_with_permission_msg_fallback():
    """ProgrammingError message 含 'permission' → 仍走 permission（边界保留）。"""
    exc = psycopg2.ProgrammingError("permission denied for table foo")
    assert _classify_psycopg2_error(exc) == "permission"


def test_classify_programming_error_with_syntax_msg_fallback():
    """ProgrammingError message 含 'syntax' → 仍走 syntax（边界保留）。"""
    exc = psycopg2.ProgrammingError("syntax error at or near SELECT")
    assert _classify_psycopg2_error(exc) == "syntax"


def test_classify_unknown_programming_error_falls_back_to_object():
    """未识别的 ProgrammingError 子类 → 'object'（向后兼容，老 caller 继续工作）。"""
    # DuplicateAlias / CheckViolation 之类少见错落 object 兜底
    exc = psycopg2.ProgrammingError("some unclassified error")
    assert _classify_psycopg2_error(exc) == "object"