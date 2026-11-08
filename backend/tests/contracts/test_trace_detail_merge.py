"""trace 明细的「输出合并」（plan 2026-11-08-trace-inspector D1）。

`@traced_node` 会为每个节点再开一个 DATA span（名字形如 `sql_plan_output`），节点产出放在
它的 **input** 里。不合并的话：页面点开节点看到「输出：空」（最想看的东西不在），
而且一轮 43 个 span 里一半是这种内部摘要。
"""
from __future__ import annotations

import pytest

from app.api.observability import _merge_output_spans

pytestmark = pytest.mark.contracts


def _span(span_id: str, name: str, *, parent: str | None = None,
          span_type: str = "NODE", input=None, output=None) -> dict:
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "span_name": name,
        "span_type": span_type,
        "input": input,
        "output": output,
    }


class TestMergeOutputSpans:
    def test_把子节点的产出并进父节点输出(self) -> None:
        spans = [
            _span("s1", "sql_generate", input="prompt"),
            _span("s2", "sql_generate_output", parent="s1", span_type="DATA", input="SELECT 1"),
        ]

        merged = _merge_output_spans(spans)

        assert [s["span_id"] for s in merged] == ["s1"]
        assert merged[0]["output"] == "SELECT 1"
        assert merged[0]["input"] == "prompt"

    def test_多个输出合并成列表(self) -> None:
        spans = [
            _span("s1", "validate"),
            _span("s2", "validate_output", parent="s1", span_type="DATA", input="a"),
            _span("s3", "validate_output", parent="s1", span_type="DATA", input="b"),
        ]

        merged = _merge_output_spans(spans)

        assert len(merged) == 1
        assert merged[0]["output"] == ["a", "b"]

    def test_原本就有输出时都保留(self) -> None:
        spans = [
            _span("s1", "report_agent", output="已有"),
            _span("s2", "report_agent_output", parent="s1", span_type="DATA", input="补充"),
        ]

        merged = _merge_output_spans(spans)

        assert merged[0]["output"] == ["已有", "补充"]

    def test_没有子输出的节点原样(self) -> None:
        spans = [_span("s1", "security_guard", input="q")]

        merged = _merge_output_spans(spans)

        assert merged == spans
        assert merged[0]["output"] is None

    def test_孤立的数据节点不会被丢掉(self) -> None:
        """没有 parent 的 DATA span 留在列表里（宁可多一个也不要静默丢数据）。"""
        spans = [_span("s1", "orphan_output", span_type="DATA", input="x")]

        merged = _merge_output_spans(spans)

        assert len(merged) == 1
        assert merged[0]["span_id"] == "s1"

    def test_不修改入参结构(self) -> None:
        spans = [
            _span("s1", "sql_plan"),
            _span("s2", "sql_plan_output", parent="s1", span_type="DATA", input="plan"),
        ]
        original = [dict(s) for s in spans]

        _merge_output_spans(spans)

        # 列表被过滤了，但父 span 的 input 不该被改写
        assert spans[0]["input"] == original[0]["input"]
