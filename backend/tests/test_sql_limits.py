"""SQL 工具防护层测试：行数上限 + 超时 + 错误分类。

execute_sql 与 validate_sql 必须：
- 返回 error_kind 字段供上游分类
- 返回 truncated/row_count 让 LLM 知道结果被截
- 永远不会把 conn.close 漏掉导致连接泄露
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.smoke


# --- _classify_psycopg2_error ---------------------------------------------------

def _exc(kind):
    """Construct a fake psycopg2 exception with the right MRO so isinstance works."""
    base = type("FakeExc", (), {}) if kind == "other" else None
    # psycopg2 的异常类用 isinstance 匹配；为不依赖 psycopg2 实际安装路径，
    # 我们用 type 构造一个与目标类同名的类，继承 psycopg2 对应异常。
    import psycopg2.errors
    cls = getattr(psycopg2.errors, kind, None) or psycopg2.Error
    return cls("test")


def test_classify_timeout():
    from app.tools.sql_tools import _classify_psycopg2_error
    import psycopg2.errors
    e = psycopg2.errors.QueryCanceled("canceling statement due to statement timeout")
    assert _classify_psycopg2_error(e) == "timeout"


def test_classify_connection():
    from app.tools.sql_tools import _classify_psycopg2_error
    import psycopg2.errors
    e = psycopg2.errors.OperationalError("server closed the connection unexpectedly")
    assert _classify_psycopg2_error(e) == "connection"


def test_classify_syntax():
    from app.tools.sql_tools import _classify_psycopg2_error
    import psycopg2.errors
    e = psycopg2.errors.SyntaxError("syntax error at or near \"FROM\"")
    assert _classify_psycopg2_error(e) == "syntax"


def test_classify_undefined_column_is_object():
    from app.tools.sql_tools import _classify_psycopg2_error
    import psycopg2.errors
    e = psycopg2.errors.UndefinedColumn('column "x" does not exist')
    assert _classify_psycopg2_error(e) == "object"


def test_classify_permission_in_programmingerror():
    from app.tools.sql_tools import _classify_psycopg2_error
    import psycopg2.errors
    e = psycopg2.ProgrammingError("permission denied for table fact_sales")
    assert _classify_psycopg2_error(e) == "permission"


# --- execute_sql truncation / error envelope ------------------------------------

def test_execute_sql_truncates_at_5000_rows(monkeypatch):
    from app.tools import sql_tools

    fake_rows = [{"id": i, "v": i * 1.0, "_total": 5001} for i in range(5001)]

    class FakeCursor:
        description = [SimpleNamespace(name="id", type_code=23), SimpleNamespace(name="v", type_code=1700)]

        def execute(self, sql):
            pass

        def fetchall(self):
            # 最后一行是 _total
            return fake_rows

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self, cursor_factory=None):
            return FakeCursor()

        def close(self):
            pass

    monkeypatch.setattr(sql_tools, "_get_pg_conn", lambda: FakeConn())

    result = json.loads(sql_tools.execute_sql("SELECT * FROM fact_sales"))
    assert result["truncated"] is True
    assert result["row_count"] == 5001
    assert len(result["rows"]) == 5000


def test_execute_sql_untruncated_when_under_cap(monkeypatch):
    from app.tools import sql_tools

    class FakeCursor:
        description = [SimpleNamespace(name="id", type_code=23)]

        def execute(self, sql): pass
        def fetchall(self):
            return [{"id": 1, "_total": 6}, {"id": 2, "_total": 6}, {"id": 3, "_total": 6},
                    {"id": 4, "_total": 6}, {"id": 5, "_total": 6}, {"id": 6, "_total": 6}]

        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self, cursor_factory=None): return FakeCursor()
        def close(self): pass

    monkeypatch.setattr(sql_tools, "_get_pg_conn", lambda: FakeConn())
    result = json.loads(sql_tools.execute_sql("SELECT * FROM dim_date"))
    assert result["truncated"] is False
    assert result["row_count"] == 6
    assert len(result["rows"]) == 6


def test_execute_sql_timeout_returns_classified_error(monkeypatch):
    from app.tools import sql_tools
    import psycopg2.errors

    class FakeCursor:
        description = None

        def execute(self, sql):
            raise psycopg2.errors.QueryCanceled("canceling statement due to statement timeout")

        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self, cursor_factory=None): return FakeCursor()
        def close(self): pass

    monkeypatch.setattr(sql_tools, "_get_pg_conn", lambda: FakeConn())
    # 注意：不能用 SELECT pg_sleep(...) 触发超时——A-1 危险函数黑名单会在
    # AST 闸直接拦下（见 test_sql_safety_gate.py）。超时分类用正常业务 SQL
    # + FakeCursor 抛 QueryCanceled 模拟。
    result = json.loads(sql_tools.execute_sql("SELECT * FROM fact_sales"))
    assert result["error_kind"] == "timeout"
    assert result["row_count"] == 0
    assert result["rows"] == []
    assert "timeout" in result["error"] or "cancel" in result["error"]


def test_execute_sql_connection_error_classified(monkeypatch):
    from app.tools import sql_tools
    import psycopg2.errors

    class FakeCursor:
        description = None
        def execute(self, sql):
            raise psycopg2.errors.OperationalError("server closed the connection")
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class FakeConn:
        def cursor(self, cursor_factory=None): return FakeCursor()
        def close(self): pass

    monkeypatch.setattr(sql_tools, "_get_pg_conn", lambda: FakeConn())
    result = json.loads(sql_tools.execute_sql("SELECT 1"))
    assert result["error_kind"] == "connection"


def test_validate_sql_sets_error_kind_too(monkeypatch):
    from app.tools import sql_tools
    import psycopg2.errors

    class FakeCursor:
        def execute(self, sql):
            raise psycopg2.errors.SyntaxError("syntax error")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def cursor(self): return FakeCursor()
        def close(self): pass

    monkeypatch.setattr(sql_tools, "_get_pg_conn", lambda: FakeConn())
    result = json.loads(sql_tools.validate_sql("SELECT"))
    assert result["valid"] is False
    assert result["error_kind"] == "syntax"


# ── ANALYSIS_DSN 解析 + ragent_readonly 真连断言 ──────────────────────
# docs/plans/2026-08-05-pg-role-least-privilege.md


def test_analysis_dsn_falls_back_to_pg_dsn(monkeypatch):
    """ANALYSIS_DSN 未设置 → 回退到 PG_DSN，向后兼容。"""
    monkeypatch.delenv("ANALYSIS_DSN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://only-via-dsn@host/db")
    # 重新加载模块以触发常量重读
    import importlib
    import app.tools.sql_tools as sql_tools_mod
    importlib.reload(sql_tools_mod)
    assert sql_tools_mod._analysis_dsn() == "postgresql://only-via-dsn@host/db"
    importlib.reload(sql_tools_mod)  # 还原


def test_analysis_dsn_overrides_when_set(monkeypatch):
    """ANALYSIS_DSN 优先于 DATABASE_URL——ragent_readonly 生效路径。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://ragent:ragent@host/db")
    monkeypatch.setenv("ANALYSIS_DSN", "postgresql://ragent_readonly:ro@host/db")
    import importlib
    import app.tools.sql_tools as sql_tools_mod
    importlib.reload(sql_tools_mod)
    assert sql_tools_mod._analysis_dsn() == "postgresql://ragent_readonly:ro@host/db"
    importlib.reload(sql_tools_mod)  # 还原


def test_get_pg_conn_uses_analysis_dsn(monkeypatch):
    """_get_pg_conn 必须走 ANALYSIS_DSN（深度防御），不直连 PG_DSN。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://ragent:super@host/db")
    monkeypatch.setenv("ANALYSIS_DSN", "postgresql://ragent_readonly:ro@host/db")

    captured = {}

    def fake_psycopg2_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs

        class _Cursor:
            def execute(self, sql): pass
            def close(self): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Conn:
            def cursor(self, cursor_factory=None): return _Cursor()
            def close(self): pass
        return _Conn()

    monkeypatch.setattr("app.tools.sql_tools.psycopg2.connect", fake_psycopg2_connect)
    import importlib
    import app.tools.sql_tools as sql_tools_mod
    importlib.reload(sql_tools_mod)
    conn = sql_tools_mod._get_pg_conn()
    conn.close()
    assert "ragent_readonly" in captured["dsn"], f"未走 ANALYSIS_DSN: {captured['dsn']}"
    importlib.reload(sql_tools_mod)  # 还原
