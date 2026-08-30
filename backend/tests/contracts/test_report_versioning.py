"""P10 report/versioning.py 契约：三态 → 存储状态映射（fail-closed）。

append-only 不变量由既有 tests/test_sql_error_envelope.py 三态落库路由钉 +
真 e2e（P12 手动门）覆盖；本模块钉纯函数语义。
"""
from __future__ import annotations

import pytest

from app.report.versioning import resolve_report_status


def test_success_and_empty_resolve_to_done():
    assert resolve_report_status("SUCCESS") == "done"
    assert resolve_report_status("EMPTY") == "done"


def test_failed_resolves_to_error():
    assert resolve_report_status("FAILED") == "error"


def test_unknown_status_fails_closed():
    """未知 execution_status → error（fail-closed：不伪造成功）。"""
    assert resolve_report_status("RUNNING") == "error"
    assert resolve_report_status("") == "error"
    assert resolve_report_status("weird") == "error"


def test_service_persists_resolved_status():
    """report_version_service 的 report_status 字面量已换源（消费 resolver）。"""
    import inspect

    from app.services import report_version_service

    src = inspect.getsource(report_version_service)
    assert "resolve_report_status" in src
