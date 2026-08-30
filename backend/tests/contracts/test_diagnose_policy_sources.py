"""P9 反向钉：DiagnosePolicy 的 kind 域与 recoverable 判定必须与 reliability.errors 同源。

防止 errors.py 表与 DiagnosePolicy 内联逻辑未来单向漂移（P8 衔接注记：收编单一来源）。
"""
from __future__ import annotations

import app.agent.sql_graph as sql_graph_module
from app.reliability import errors
from app.reliability.errors import (
    AGENT_RECOVERABLE_KINDS,
    SQL_ERROR_KINDS,
    agent_recoverable,
)


def test_diagnose_policy_kind_vocabulary_is_errors_source():
    """源同源钉：DiagnosePolicy normalize 用的白名单必须是 errors.SQL_ERROR_KINDS 本尊。"""
    assert sql_graph_module.SQL_ERROR_KINDS is errors.SQL_ERROR_KINDS
    assert set(SQL_ERROR_KINDS) == {"syntax", "object", "timeout", "connection", "permission", "other"}


def test_diagnose_policy_fail_branch_uses_agent_recoverable_source():
    """源同源钉：fail 分支判定必须是 errors.agent_recoverable 本尊。"""
    assert sql_graph_module.agent_recoverable is errors.agent_recoverable
    assert set(SQL_ERROR_KINDS) - set(AGENT_RECOVERABLE_KINDS) == {"timeout", "connection", "permission"}


def test_diagnose_policy_decisions_match_agent_recoverable_table():
    """行为表钉（重构前后必须一致）：非 agent-recoverable kind → fail，其余预算内 retry。"""
    for kind in SQL_ERROR_KINDS:
        decision = sql_graph_module.DiagnosePolicy.decide(error_kind=kind)
        if agent_recoverable(kind):
            assert decision.action == "retry_sql"
            assert decision.recoverable is True
        else:
            assert decision.action == "fail"
            assert decision.recoverable is False
