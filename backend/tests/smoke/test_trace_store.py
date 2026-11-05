"""思考链路落库的 payload 构造（plan 2026-11-05-trace-persistence）。

这里只钉**纯函数**：SSE trace 帧 → `app.conversations.metadata` 的形状。
真正的写入（asyncpg）由 persistence 用例覆盖，不在 smoke 里碰库。
"""
from __future__ import annotations

import json

import pytest

from app.infra.execution import trace_store

pytestmark = pytest.mark.smoke


def frame(step: str, status: str, **extra: object) -> dict:
    return {
        "event": "trace",
        "data": json.dumps({"step": step, "status": status, **extra}, ensure_ascii=False),
    }


def test_collect_frame_only_takes_trace_frames() -> None:
    assert trace_store.collect_frame(frame("安全检查", "success"), ts=1000)["step"] == "安全检查"
    assert trace_store.collect_frame({"event": "report", "data": "{}"}) is None
    assert trace_store.collect_frame({"event": "trace", "data": "{not json"}) is None
    assert trace_store.collect_frame({"event": "trace", "data": json.dumps({"status": "success"})}) is None
    assert trace_store.collect_frame(None) is None


def test_build_trace_metadata_maps_sse_fields_to_timeline_entries() -> None:
    frames = [
        trace_store.collect_frame(frame("安全检查", "success", detail="通过（LOW）", kind="agent"), ts=1000),
        trace_store.collect_frame(
            frame("理解意图 · 思考", "success", detail="模型原文 12 字", kind="agent", reasoning="原文"),
            ts=4000,
        ),
    ]
    meta = trace_store.build_trace_metadata(frames)
    assert meta is not None
    assert meta["duration_ms"] == 3000
    assert meta["status"] == "done"
    assert [entry["nodeName"] for entry in meta["entries"]] == ["安全检查", "理解意图 · 思考"]
    assert meta["entries"][0]["detail"] == "通过（LOW）"
    assert meta["entries"][0]["kind"] == "agent"
    assert meta["entries"][1]["reasoning"] == "原文"


def test_build_trace_metadata_dedupes_by_node_keeping_first_timestamp() -> None:
    frames = [
        trace_store.collect_frame(frame("解析需求", "running"), ts=1000),
        trace_store.collect_frame(frame("解析需求", "success", detail="时间 2024年"), ts=5000),
    ]
    entries = trace_store.build_trace_metadata(frames)["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "success"
    assert entries[0]["detail"] == "时间 2024年"
    # 耗时要按「这一步什么时候开始」算，不是按最后一帧到达算
    assert entries[0]["ts"] == 1000


def test_build_trace_metadata_returns_none_without_usable_frames() -> None:
    assert trace_store.build_trace_metadata([]) is None
    assert trace_store.build_trace_metadata(None) is None
    assert trace_store.build_trace_metadata([{"step": "", "status": "success"}]) is None
    assert trace_store.build_trace_metadata([{"step": "x", "status": "bogus"}]) is None


def test_reasoning_is_clipped_to_the_same_budget_as_sse() -> None:
    frames = [trace_store.collect_frame(frame("理解意图 · 思考", "success", reasoning="x" * 5000), ts=1)]
    entry = trace_store.build_trace_metadata(frames)["entries"][0]
    assert len(entry["reasoning"]) == trace_store.MAX_REASONING_CHARS


def test_blank_reasoning_is_dropped() -> None:
    frames = [trace_store.collect_frame(frame("安全检查", "success", reasoning="   "), ts=1)]
    assert "reasoning" not in trace_store.build_trace_metadata(frames)["entries"][0]
