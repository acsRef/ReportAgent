"""P15 reliability 收口 ⑨：DB timeout contract（真 PG，短超时 override，不真等 30s）。

验证链：statement timeout 触发 → `_classify_psycopg2_error` → kind=timeout →
DiagnosePolicy `fail`（agent 侧不盲 retry，⑦ 已钉）→ execute_sql 返回空结果（无假行）。
把 `STATEMENT_TIMEOUT_MS` override 成 100ms，跑一条必然超时的重查询（generate_series），
不需要等真实 30s。connect-timeout 接线用结构钉（不真连不可达主机）。
"""
from __future__ import annotations

import json

import pytest

from app.tools import sql_tools

pytestmark = pytest.mark.persistence


def _run_heavy_query() -> dict:
    """跑一条必然超过 100ms 的查询（generate_series 大数求和，无表 → 过安全闸）。"""
    sql = "SELECT sum(g) FROM generate_series(1, 200000000) AS g"
    return json.loads(sql_tools.execute_sql(sql))


def test_db_statement_timeout_classified_and_no_fake_rows(monkeypatch):
    """statement timeout → error_kind=timeout，rows 空（timeout 不得有部分/假行）。"""
    monkeypatch.setattr(sql_tools, "STATEMENT_TIMEOUT_MS", 100)  # 0.1s 触发
    out = _run_heavy_query()
    assert out.get("error_kind") == "timeout", (
        f"statement timeout 应分类为 timeout（QueryCanceled）：{out.get('error_kind')}"
    )
    assert "timeout" in (out.get("error") or "").lower(), (
        f"错误文本应带 timeout 语义: {out.get('error')}"
    )
    assert out.get("rows") == [], "timeout 不得返回部分/伪造行"
    assert out.get("row_count", 0) == 0, "timeout 不得有 row_count"


def test_db_statement_timeout_respects_budget_terminal(monkeypatch):
    """timeout 在 DiagnosePolicy 是 fail（非 recoverable → 不 retry_sql/replan）。"""
    from app.agent.sql_graph import DiagnosePolicy

    d = DiagnosePolicy.decide(
        error_kind="timeout", retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert d.action == "fail", f"DB timeout 不得盲 retry（无信息增益）：{d.action}"


def test_connect_timeout_and_statement_wired(monkeypatch):
    """_get_pg_conn 接线：connect_timeout=CONNECT_TIMEOUT_S + statement_timeout options。"""
    import psycopg2

    captured: dict = {}

    def _fake_connect(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop before real connect")

    monkeypatch.setattr(psycopg2, "connect", _fake_connect)
    monkeypatch.setattr(sql_tools, "CONNECT_TIMEOUT_S", 7)
    monkeypatch.setattr(sql_tools, "STATEMENT_TIMEOUT_MS", 1234)
    with pytest.raises(RuntimeError):
        sql_tools._get_pg_conn()
    assert captured.get("connect_timeout") == 7, "connect_timeout 必须接 CONNECT_TIMEOUT_S"
    assert "statement_timeout=1234" in captured.get("options", ""), (
        "statement_timeout 必须经 options 落到 PG 连接"
    )
