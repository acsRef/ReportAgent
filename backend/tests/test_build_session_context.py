"""第 5 轮·build_session_context glue + prompt 注入测试。

存储/LLM 全部 mock，验证：无压缩路径不落库；压缩路径回写 digest 并把抽取事实
存进 L3；sql_graph._plan/_generate_sql 与 requirement_parser 会把 conversation_context
前置进 prompt。

P3 Task 4 修订：facade `app.context.build_session_context` 内部转发到
`_engine._prepare_conversation_context`；其 compress_and_extract 走 _engine module
globals，monkeypatch 必须打到 `_engine`。
"""
from __future__ import annotations

import pytest

from app import context
from app.agent import requirement_parser, sql_graph
# P4a：compress_and_extract 实现在 app.memory.conversation；build_context 解析其 globals，
# patch target 须随之改（沿用 _engine 别名最小化改动）。L3 写入路径：
# prepare_conversation_context → memory.manager.remember_conversation_facts →
# MemoryManager.remember_preference → UserMemory.save（下方 um_mod patch 仍拦截）。
from app.memory import conversation as _engine
from app.infra.checkpoint.session import session_manager
from app.infra.conversation import repository as conv_repo
from app.infra.memory import user_memory as um_mod

pytestmark = pytest.mark.smoke


def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


_ZERO = {"digest": None, "digest_msg_count": 0, "digest_version": 0, "mid_digest": None}


async def test_build_session_context_no_compress_no_persist(monkeypatch):
    saved = []

    async def fake_get_messages(sid, uid):
        return _msgs(5)

    async def fake_get_ctx(sid):
        return dict(_ZERO)

    async def fake_save_ctx(sid, updates):
        saved.append((sid, updates))

    monkeypatch.setattr(conv_repo, "get_messages", fake_get_messages)
    monkeypatch.setattr(session_manager, "get_context_state", fake_get_ctx)
    monkeypatch.setattr(session_manager, "save_context_state", fake_save_ctx)

    ctx = await context.build_session_context("s1", 1)
    assert "m0" in ctx and "m4" in ctx
    assert "<对话摘要>" not in ctx  # 5 条不触发压缩
    assert saved == []            # 无压缩 → 不回写


async def test_build_session_context_compress_persists_and_saves_l3(monkeypatch):
    saved_ctx = []
    saved_facts = []

    async def fake_get_messages(sid, uid):
        return _msgs(22)

    async def fake_get_ctx(sid):
        return dict(_ZERO)

    async def fake_save_ctx(sid, updates):
        saved_ctx.append((sid, updates))

    async def fake_um_save(self, **kw):
        saved_facts.append(kw)
        return 1

    monkeypatch.setattr(conv_repo, "get_messages", fake_get_messages)
    monkeypatch.setattr(session_manager, "get_context_state", fake_get_ctx)
    monkeypatch.setattr(session_manager, "save_context_state", fake_save_ctx)
    monkeypatch.setattr(um_mod.UserMemory, "save", fake_um_save)
    monkeypatch.setattr(
        _engine, "compress_and_extract",
        lambda old, batch: {
            "summary": "摘要X",
            "extracted_schemas": [{"type": "field_mapping", "user_term": "销售额", "db_field": "total_amount"}],
            "extracted_preferences": ["用户偏好柱状图"],
        },
    )

    ctx = await context.build_session_context("s2", 1)
    # 上下文含摘要块
    assert "<对话摘要>\n摘要X" in ctx
    # digest 状态被回写
    assert saved_ctx and saved_ctx[0][1]["digest"] == "摘要X"
    assert saved_ctx[0][1]["digest_msg_count"] == 12
    # L3 事实被保存（schema dict + preference，mem0 默认关闭不额外增加）
    contents = {f["content"] for f in saved_facts}
    assert "用户偏好柱状图" in contents
    assert any("total_amount" in c for c in contents)


# --- prompt 注入 -------------------------------------------------------------


def test_sql_plan_prepends_conversation_context(monkeypatch):
    captured = {}
    monkeypatch.setattr(sql_graph, "call_llm", lambda prompt, **k: captured.setdefault("p", prompt) or "{}")
    sql_graph._plan({
        "user_query": "再看看", "schema_context": None, "query_plan": None,
        "conversation_context": "历史上下文XYZ",
    })
    assert "历史上下文XYZ" in captured["p"]
    assert "可用表结构」为准" in captured["p"]  # 防字段类型漂移的权威声明


def test_sql_plan_no_context_block_when_absent(monkeypatch):
    captured = {}
    monkeypatch.setattr(sql_graph, "call_llm", lambda prompt, **k: captured.setdefault("p", prompt) or "{}")
    sql_graph._plan({"user_query": "q", "schema_context": None, "query_plan": None})
    assert "<对话上下文>" not in captured["p"]


def test_requirement_parser_prepends_conversation_context(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        requirement_parser, "call_llm",
        lambda prompt, **k: captured.setdefault("p", prompt) or "not-json",
    )
    requirement_parser.parse_requirement(
        user_query="再按产品细分", schema_context=None, conversation_context=" prior-range-2024",
    )
    assert " prior-range-2024" in captured["p"]
    assert "可用表结构」为准" in captured["p"]  # 防字段类型漂移的权威声明
