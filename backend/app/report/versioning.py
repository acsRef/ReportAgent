"""ReportVersion 域语义（P10，report-runtime.md §三）。

三态 verdict（SUCCESS / EMPTY / FAILED）→ 存储状态（done / error）的单一映射。
append-only 不变量（同 session version 单调递增、三态全部落行）由
tests/test_sql_error_envelope.py 三态路由钉 + 真实 e2e（P12 手动门）覆盖。

fail-closed：未知 execution_status → error——永不伪造成功（宪法 §10）。
"""
from __future__ import annotations

from typing import Literal

_ReportStatus = Literal["done", "error"]


def resolve_report_status(execution_status: str) -> _ReportStatus:
    """SUCCESS/EMPTY 落 done（EMPTY 是合法零行，历史版本可查）；其余一律 error。"""
    if execution_status in ("SUCCESS", "EMPTY"):
        return "done"
    return "error"
