"""sql_graph 输出层测试：_build_output 三态 + _generate_sql 重试反馈。

覆盖 bug-review B-2 / query-execution-safety 层2 / legacy-sql-bugs Bug1+Bug2：
- 合法零行必须写 EMPTY（而非被误判 FAILED / 或永远写不出 EMPTY）
- 截断后 row_count 必须保留 CTE count(*) 的真实总数
- error_kind 必须透传到 QueryResult 与 ErrorDetail
- execute 阶段失败（validate 通过但 execute 报错）必须喂回重试 prompt
"""
from __future__ import annotations

import json

import pytest

from app.agent import sql_graph

pytestmark = pytest.mark.graphs


def _build(sql_result: dict) -> "sql_graph.QueryResult":
    state = {"sql_result": json.dumps(sql_result), "generated_sql": "SELECT 1"}
    return sql_graph._build_output(state)["query_result"]


def test_build_output_writes_empty_for_legitimate_zero_match():
    qr = _build({"columns": [{"name": "x", "type": "int"}], "rows": [],
                 "row_count": 0, "truncated": False})
    assert qr.status == "EMPTY"
    assert qr.row_count == 0
    assert qr.error is None


def test_build_output_preserves_real_row_count_after_truncation():
    rows = [{"id": i} for i in range(5001)]
    qr = _build({"columns": [{"name": "id", "type": "int"}], "rows": rows,
                 "row_count": 50000, "truncated": True})
    assert qr.status == "SUCCESS"
    assert qr.row_count == 50000          # 不是 len(rows)==5001
    assert qr.truncated is True


def test_build_output_failed_carries_error_kind():
    qr = _build({"columns": [], "rows": [], "row_count": 0,
                 "error": 'column "x" does not exist', "error_kind": "object"})
    assert qr.status == "FAILED"
    assert qr.error_kind == "object"
    assert qr.error is not None and qr.error.kind == "object"


def test_build_output_success_with_rows():
    qr = _build({"columns": [{"name": "id", "type": "int"}],
                 "rows": [{"id": 1}, {"id": 2}], "row_count": 2, "truncated": False})
    assert qr.status == "SUCCESS"
    assert qr.row_count == 2


def test_generate_sql_feeds_execute_error_back_to_prompt(monkeypatch):
    """Bug1：validate 通过但 execute 失败时，重试 prompt 必须带上执行错误。"""
    captured = {}

    def fake_call_llm(prompt, **kwargs):
        # _generate_sql 传入 [{"role":"user","content":prompt}]
        captured["prompt"] = prompt[0]["content"] if isinstance(prompt, list) else prompt
        return "SELECT 1"

    monkeypatch.setattr(sql_graph, "call_llm", fake_call_llm)

    state = {
        "user_query": "q",
        "query_plan": None,
        "schema_context": None,
        "generated_sql": "SELECT nonexistent FROM fact_sales",
        "validation_result": {"valid": True},          # validate 通过
        "sql_result": json.dumps({"error": 'column "nonexistent" does not exist',
                                  "error_kind": "object"}),  # 但 execute 失败
        "retry_counters": {"sql_generation": 1, "plan": 0},
    }
    sql_graph._generate_sql(state)
    assert "does not exist" in captured["prompt"]
    assert "nonexistent" in captured["prompt"]
