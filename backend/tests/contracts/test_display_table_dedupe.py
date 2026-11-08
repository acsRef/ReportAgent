"""重复列的行键也要清掉（plan 2026-11-08-analysis-quality-cleanup D2）。

表格层 `drop_duplicate_columns` 早已按「整列取值完全相同」裁掉重复列定义，
但 `answer.table.rows` 里仍留着两个同值键：`localize_chart` 会把它们改成同一个中文名，
图表上就出现两个同名系列 / 轴重复。

同时钉住 `_decode_requirement` 的状态覆盖（D3）：草稿行 locked 后，
`payload.status` 必须跟着变——否则刷新回来 pill 又显示「信息完整 · 待确认」。
"""
from __future__ import annotations

import json

import pytest

from app.report.labels import build_display_table, localize_chart
from app.services.snapshot_service import _decode_requirement

pytestmark = pytest.mark.contracts


class TestDuplicateColumnRows:
    def test_重复列的键从行里删掉(self) -> None:
        columns = ["区域", "region", "销售额"]
        rows = [
            {"区域": "华东", "region": "华东", "销售额": "100"},
            {"区域": "华南", "region": "华南", "销售额": "200"},
        ]

        table, _label_map = build_display_table(columns, rows)

        assert [col["key"] for col in table["columns"]] == ["区域", "销售额"]
        assert all("region" not in row for row in table["rows"])
        assert all("区域" in row for row in table["rows"])

    def test_没有重复列时行原样(self) -> None:
        columns = ["区域", "销售额"]
        rows = [{"区域": "华东", "销售额": "100"}]

        table, _ = build_display_table(columns, rows)

        assert table["rows"] == rows

    def test_图表不会出现两个同名系列(self) -> None:
        columns = ["区域", "region", "销售额"]
        rows = [
            {"区域": "华东", "region": "华东", "销售额": "100"},
            {"区域": "华南", "region": "华南", "销售额": "200"},
        ]
        table, label_map = build_display_table(columns, rows)
        chart = {
            "type": "pie",
            "config": {
                "dimensions": {"category": "区域", "value": "销售额"},
                "data": [dict(row) for row in table["rows"]],
            },
        }

        localized = localize_chart(chart, label_map)
        keys = sorted(localized["config"]["data"][0].keys())

        assert keys == ["区域", "销售额"]
        assert len(keys) == len(set(keys))

    def test_三列重复只留先出现的那个(self) -> None:
        columns = ["月份", "month", "sales", "销售额"]
        rows = [
            {"月份": "1", "month": "1", "sales": "10", "销售额": "10"},
        ]

        table, _ = build_display_table(columns, rows)

        # 「month」与「月份」同值 → 删；「销售额」与先出现的「sales」同值 → 删
        assert [col["key"] for col in table["columns"]] == ["月份", "sales"]


class TestRequirementStatusFollowsRow:
    """D3 后端半边：快照里的卡状态以草稿行为准。"""

    def _row(self, status: str, payload_status: str) -> dict:
        return {
            "id": 1,
            "session_id": "s",
            "user_id": 1,
            "version": 2,
            "status": status,
            "payload": json.dumps({"id": "c", "status": payload_status, "summary": "s"}),
            "confirmed_at": None,
        }

    def test_locked_覆盖_payload_里的_complete(self) -> None:
        decoded = _decode_requirement(self._row("locked", "complete"))

        assert decoded is not None
        assert decoded["status"] == "locked"
        assert decoded["payload"]["status"] == "locked"

    def test_已经一致时不动(self) -> None:
        decoded = _decode_requirement(self._row("complete", "complete"))

        assert decoded is not None
        assert decoded["payload"]["status"] == "complete"

    def test_payload_是字符串也能处理(self) -> None:
        decoded = _decode_requirement(self._row("locked", "missing"))

        assert decoded is not None
        assert decoded["payload"]["status"] == "locked"

    def test_没有草稿返回_None(self) -> None:
        assert _decode_requirement(None) is None
