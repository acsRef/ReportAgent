"""第 1 轮·分层对话上下文核心逻辑测试。

覆盖 build_context 的窗口/压缩触发/覆盖重写/L2.5 归档，以及 compress_and_extract
的 800 字硬上限、结构化事实透传、坏 JSON 兜底。LLM 用 monkeypatch 隔离。

P3 Task 4 修订：旧 `app.context` module 升级为 package；`compress_and_extract` /
`call_llm` 等内部函数实际位于 `app.context._engine` 子模块，monkeypatch 必须打到
实际定义所在 module（`_engine`），而非 facade（`app.context`）。facade 仍 re-export
这些名字以保外部 import 兼容。
"""
from __future__ import annotations

import json

import pytest

from app import context
from app.context import _engine

pytestmark = pytest.mark.smoke


def _msgs(n: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
        for i in range(n)
    ]


# --- format_messages ---------------------------------------------------------


def test_format_messages_renders_role_content():
    assert context.format_messages(
        [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "在的"}]
    ) == "user: 你好\nassistant: 在的"


def test_format_messages_skips_empty():
    assert context.format_messages(
        [{"role": "user", "content": ""}, {"role": "user", "content": "有料"}]
    ) == "user: 有料"


def test_format_context_block_declares_schema_authoritative():
    """护栏：上下文块必须声明 schema 为字段名/类型的权威来源，防压缩导致类型漂移。"""
    block = context.format_context_block("CTX")
    assert "CTX" in block
    assert "可用表结构」为准" in block


# --- build_context：无压缩路径 -------------------------------------------------


def test_build_context_no_compress_under_threshold():
    ctx, updates, batch = context.build_context(messages=_msgs(20))
    assert updates == {}
    assert batch == []
    assert "消息0" in ctx and "消息19" in ctx
    assert "<对话摘要>" not in ctx  # 没压缩就没有摘要块


# --- build_context：压缩触发 + 覆盖重写 ---------------------------------------


def test_build_context_triggers_compress_and_replaces(monkeypatch):
    """覆盖重写：新摘要替换旧摘要，绝不追加。"""
    monkeypatch.setattr(
        _engine, "compress_and_extract",
        lambda old, batch: {"summary": "新摘要", "extracted_schemas": [], "extracted_preferences": []},
    )
    ctx, updates, batch = context.build_context(
        messages=_msgs(22), digest="旧摘要", digest_msg_count=0, digest_version=0,
    )
    # 22 条 → recent=后 10 条，batch=前 12 条
    assert batch == _msgs(22)[:12]
    assert updates["digest"] == "新摘要"          # 替换，不是 "旧摘要...新摘要"
    assert "旧摘要" not in ctx
    assert "<对话摘要>\n新摘要" in ctx
    assert updates["digest_msg_count"] == 12
    assert updates["digest_version"] == 1


def test_build_context_no_recompress_when_no_new_batch(monkeypatch):
    """digest_msg_count 已追上 → 不再压缩。"""
    called = {"n": 0}

    def _spy(old, batch):
        called["n"] += 1
        return {"summary": "x", "extracted_schemas": [], "extracted_preferences": []}

    monkeypatch.setattr(_engine, "compress_and_extract", _spy)
    # 22 条，但 digest_msg_count=12 已 == old_count(12) → 不压缩
    ctx, updates, batch = context.build_context(
        messages=_msgs(22), digest="已有摘要", digest_msg_count=12, digest_version=1,
    )
    assert called["n"] == 0
    assert updates == {}
    assert "<对话摘要>\n已有摘要" in ctx


# --- build_context：L2.5 归档 --------------------------------------------------


def test_build_context_archives_l2_5_at_interval(monkeypatch):
    monkeypatch.setattr(
        _engine, "compress_and_extract",
        lambda old, batch: {"summary": "S" * 100, "extracted_schemas": [], "extracted_preferences": []},
    )
    # digest_version=4 → 压缩后 =5，命中 L2_ARCHIVE_INTERVAL → 归档 L2.5
    ctx, updates, _ = context.build_context(
        messages=_msgs(22), digest="old", digest_msg_count=0, digest_version=4,
    )
    assert updates["digest_version"] == 5
    assert updates["mid_digest"] == "S" * 100
    assert "<长期脉络>" in ctx


# --- compress_and_extract ----------------------------------------------------


def test_compress_caps_summary_to_800(monkeypatch):
    monkeypatch.setattr(
        _engine, "call_llm",
        lambda *a, **k: json.dumps({"summary": "x" * 1000}, ensure_ascii=False),
    )
    result = context.compress_and_extract(None, _msgs(5))
    assert len(result["summary"]) <= context.L2_MAX_CHARS


def test_compress_passes_through_facts(monkeypatch):
    payload = {
        "summary": "摘要",
        "extracted_schemas": [{"type": "field_mapping", "user_term": "销售额", "db_field": "total_amount"}],
        "extracted_preferences": ["用户偏好柱状图"],
    }
    monkeypatch.setattr(_engine, "call_llm", lambda *a, **k: json.dumps(payload, ensure_ascii=False))
    result = context.compress_and_extract("旧", _msgs(3))
    assert result["summary"] == "摘要"
    assert result["extracted_schemas"][0]["db_field"] == "total_amount"
    assert result["extracted_preferences"] == ["用户偏好柱状图"]


def test_compress_bad_json_falls_back_empty(monkeypatch):
    monkeypatch.setattr(_engine, "call_llm", lambda *a, **k: "这不是 JSON")
    result = context.compress_and_extract(None, _msgs(3))
    assert result["summary"] == ""
    assert result["extracted_schemas"] == []
    assert result["extracted_preferences"] == []
