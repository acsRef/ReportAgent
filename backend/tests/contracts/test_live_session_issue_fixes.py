"""真机一轮暴露的问题的确定性修复（plan 2026-11-05-live-session-issues）。

覆盖：
- D1 思考预算截断 → `ReasoningReply.truncated` / `understand_turn` 追加标注；
- D2 超时 → 不思考快速重试一次（且记录在 meta）；
- D3 `rag_schema.last_schema_error()` 如实暴露失败原因；
- D4 用户原话里的显式图表要求优先于记忆偏好。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent import turn_understanding
from app.llm.reasoning_client import ReasoningReply
from app.report.preference import (
    apply_explicit_chart_request,
    explicit_chart_type,
)
from app.tools import rag_schema
from app.tools.mcp_errors import MCPBoundaryError, MCPErrorCode

pytestmark = pytest.mark.contracts


# ── D4：显式图表要求 ──────────────────────────────────────────────────────


def _bar_chart() -> dict:
    return {
        "type": "bar",
        "config": {
            "dimensions": {"x": "月份", "y": "销售额"},
            "data": [{"月份": "1月", "销售额": 100}],
        },
    }


class TestExplicitChartType:
    def test_饼图(self) -> None:
        assert explicit_chart_type("按照月份来划分吧，然后画饼图") == "pie"
        assert explicit_chart_type("用饼状图看") == "pie"

    def test_柱状图与条形图都是bar(self) -> None:
        assert explicit_chart_type("画个柱状图") == "bar"
        assert explicit_chart_type("条形图更清楚") == "bar"

    def test_表格(self) -> None:
        assert explicit_chart_type("直接给我表格") == "table"

    def test_没提就不猜(self) -> None:
        assert explicit_chart_type("2024年各区域销售额") is None
        assert explicit_chart_type("") is None
        assert explicit_chart_type(None) is None

    def test_否定句不算要求(self) -> None:
        assert explicit_chart_type("不要表格，画图就行") is None
        assert explicit_chart_type("别用饼图") is None


class TestApplyExplicitChartRequest:
    def test_bar_改成_pie_且_dims_键归一(self) -> None:
        out = apply_explicit_chart_request(_bar_chart(), "画饼图")
        assert out["type"] == "pie"
        assert out["config"]["dimensions"] == {"category": "月份", "value": "销售额"}
        # 原对象不被改动（纯函数契约）
        assert _bar_chart()["config"]["dimensions"] == {"x": "月份", "y": "销售额"}

    def test_没有显式要求时原样返回(self) -> None:
        chart = _bar_chart()
        assert apply_explicit_chart_request(chart, "2024年各区域销售额") == chart

    def test_table_数据让位_可视化要求不改变表格(self) -> None:
        table = {"type": "table", "config": {"data": [{"a": 1}]}}
        assert apply_explicit_chart_request(table, "画饼图")["type"] == "table"


class TestUserWords:
    """confirm 流 user_query 是空串，原话在需求卡 summary（子图里是 dict）——真机踩过。"""

    def test_requirement_card_是_dict_也能取到原话(self) -> None:
        from app.agent.report_graph import _user_words

        words = _user_words({"user_query": "", "requirement_card": {"summary": "按月份看，帮我画饼图"}})
        assert "饼图" in words
        assert explicit_chart_type(words) == "pie"

    def test_requirement_card_是模型也能取到(self) -> None:
        from app.agent.report_graph import _user_words
        from app.models.requirement import RequirementCard

        card = RequirementCard(
            id="d", version=1, status="complete", summary="画个饼图",
            target_metrics=[], time_range=None, granularity=None, scope=[],
            dimensions=[], analysis_methods=[], expected_blocks=[],
            missing_fields=[], assumptions=[], confidence=0.9, confirmed_at=None,
        )
        assert explicit_chart_type(_user_words({"user_query": "", "requirement_card": card})) == "pie"

    def test_adjust_文本也参与判定(self) -> None:
        from app.agent.report_graph import _user_words

        assert explicit_chart_type(_user_words({"user_query": "", "adjustment_text": "换成柱状图"})) == "bar"


# ── D1/D2：思维链截断标注 + 超时快速重试 ───────────────────────────────────


def _reply(**kwargs: Any) -> ReasoningReply:
    base = {"content": '{"conversation_act": "new_task"}', "reasoning": "想了一半", "model": "m"}
    base.update(kwargs)
    return ReasoningReply(**base)


class TestUnderstandTurnFallbacks:
    def test_截断时追加标注(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict] = []

        def fake(prompt: str, **kwargs: Any) -> ReasoningReply:
            calls.append(kwargs)
            return _reply(truncated=True, reasoning_tokens=512)

        monkeypatch.setattr("app.llm.reasoning_client.chat_with_reasoning", fake)
        meta: dict = {}
        _plan, reasoning = turn_understanding.understand_turn("查销售额", meta=meta)

        assert turn_understanding.REASONING_TRUNCATED_MARKER in reasoning
        assert meta.get("reasoning_truncated") is True
        assert len(calls) == 1

    def test_首轮失败时用不思考模式重试一次(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[dict] = []

        def fake(prompt: str, **kwargs: Any) -> ReasoningReply | None:
            calls.append(kwargs)
            if len(calls) == 1:
                return None  # 模拟 90s read timeout
            return _reply()

        monkeypatch.setattr("app.llm.reasoning_client.chat_with_reasoning", fake)
        meta: dict = {}
        plan, _reasoning = turn_understanding.understand_turn("查销售额", meta=meta)

        assert plan is not None
        assert meta.get("retried_without_thinking") is True
        assert calls[0].get("enable_thinking") is not False
        assert calls[1]["enable_thinking"] is False
        assert calls[1]["timeout"] == turn_understanding.fast_retry_timeout()

    def test_两次都失败才回落(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(prompt: str, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr("app.llm.reasoning_client.chat_with_reasoning", fake)
        meta: dict = {}
        plan, reasoning = turn_understanding.understand_turn("查销售额", meta=meta)

        assert plan is None and reasoning == ""
        assert meta.get("failed") is True


# ── D3：schema 检索失败原因可读 ────────────────────────────────────────────


class TestLastSchemaError:
    def test_成功时清空(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rag_schema._set_schema_error("旧的失败")
        monkeypatch.setattr(rag_schema, "_retrieve_dict", lambda query, top_k: [])
        rag_schema.search_tables_from_rag("查销售额")
        assert rag_schema.last_schema_error() is None

    def test_MCP_失败时留下原因(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(query: str, top_k: int) -> list[dict]:
            raise MCPBoundaryError(MCPErrorCode.MCP_UNAVAILABLE, "连接中断")

        monkeypatch.setattr(rag_schema, "_retrieve_dict", boom)
        assert rag_schema.search_tables_from_rag("查销售额") == []
        error = rag_schema.last_schema_error()
        assert error is not None
        assert "MCP_UNAVAILABLE" in error and "连接中断" in error

    def test_意外异常也留下原因(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(query: str, top_k: int) -> list[dict]:
            raise RuntimeError("boom")

        monkeypatch.setattr(rag_schema, "_retrieve_dict", boom)
        rag_schema.search_tables_from_rag("查销售额")
        assert "RuntimeError" in (rag_schema.last_schema_error() or "")
