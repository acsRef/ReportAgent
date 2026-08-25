"""T4 补充：run_case 与 checker 的集成回归（mock httpx，不碰网络）。

背景：2026-08-25 首次跑批 16 例全 error——run_case 把 Pydantic
TurnExpectation 直接传给了吃 dict 的 check_turn。此文件钉住该接缝。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from evaluation.loader import load_all
from evaluation.runner import run_case
from evaluation.schema import BaselineCase

DATASET = "evaluation/baseline_cases.json"


def _fake_stream(events):
    return lambda *a, **kw: iter(events)


class TestRunCaseIntegration:
    def test_pydantic_expectation_does_not_crash(self):
        """回归：expectation 是 TurnExpectation 模型时 run_case 不抛 AttributeError。"""
        cases = [c for c in load_all(DATASET) if c.id == "clarify-vague-metric"]
        case = cases[0]

        client = MagicMock()
        token = "t"
        chat_events = [
            {"event": "phase", "data": {"phase": "parsing"}},
            {"event": "requirement", "data": json.dumps({
                "status": "missing",
                "missing_fields": [{"key": "time_range"}],
                "target_metrics": ["销量"],
                "time_range": None,
            })},
            {"event": "done", "data": "{}"},
        ]
        with patch("evaluation.runner._stream_sse", _fake_stream(chat_events)):
            result = run_case(case, client, token)
        assert result["status"] in ("pass", "fail")  # 不是 error
        assert result["sections"]  # 真的做了判定

    def test_latest_requirement_event_wins(self):
        """回归：PATCH 后追加的权威卡必须覆盖链路上第一帧陈旧卡。"""
        from evaluation.runner import _data_of
        events = [
            {"event": "requirement", "data": json.dumps({"status": "missing"})},
            {"event": "phase", "data": {"phase": "awaiting_confirm"}},
            {"event": "requirement", "data": json.dumps({"status": "complete"})},
        ]
        assert _data_of(events, "requirement")["status"] == "complete"


    def test_success_case_passes_end_to_end_mock(self):
        cases = {c.id: c for c in load_all(DATASET)}
        case = cases["explicit-region-sales-ranking"]
        card = {
            "status": "complete",
            "missing_fields": [],
            "target_metrics": ["销售额"],
            "time_range": "2024年",
        }
        chat_events = [
            {"event": "requirement", "data": json.dumps(card)},
            {"event": "done", "data": "{}"},
        ]

        class FakeClient(MagicMock):
            def get(self, url, **kw):
                m = MagicMock()
                m.status_code = 200
                if "/reports/" in url:
                    m.json.return_value = {"report": {
                        "query_snapshot": {"sql": "SELECT ...", "rows": [{}, {}, {}]},
                        "report_payload": {"answer": {
                            "table": {"columns": ["region"], "rows": [{}, {}]},
                            "chart": {"type": "bar"},
                        }},
                    }}
                else:  # /sessions/{sid}
                    m.json.return_value = {"session": {"report_versions": [{"version": 1}]}}
                return m

        with patch("evaluation.runner._stream_sse", _fake_stream(chat_events)), \
             patch("evaluation.runner._fill_missing_fields", side_effect=lambda c: c), \
             patch("evaluation.checker.ObservedTurn") as _:
            # confirm 流复用同一个 fake stream（返回 chat_events 即可）
            result = run_case(case, FakeClient(), "t")
        assert result["status"] == "pass", result
