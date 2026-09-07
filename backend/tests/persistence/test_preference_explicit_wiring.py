"""P16.5：explicit preference UI 语句 → active 记忆 → report 召回闭环（真 PG）。

与 api 层（离线）分工：本文件证明「用户在 UI 说『以后都用柱状图』」这条**真实写入口**
（_chat_requirement_analysis 图前短路 → remember_explicit_preference）产出的记忆是
active/high/stable_preference，且 report 档 ContextRuntime 真召回——P16 之前这条链
只能靠测试直插 active 行绕过，现在是生产路径真闭环。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.persistence


def _conn():
    import psycopg2
    return psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


async def _drive_chat(user_query: str, user_id: int, sid: str):
    """直调生产 handler（图前短路径），收集 SSE 事件。"""
    from app.main import _chat_requirement_analysis, ChatRequest
    from app.memory.manager import extract_explicit_preference  # noqa: F401  确保短路径模块可用

    req = ChatRequest(user_query=user_query, session_id=sid, mode="new")
    resp = await _chat_requirement_analysis(req, None, {"id": user_id}, session_id=sid)
    return [e async for e in resp.body_iterator]


def test_ui_preference_statement_writes_active_and_recallable(monkeypatch):
    """① UI 偏好句 → SSE idle 轻响应（不进图）+ DB active/high stable_preference 行
    ② 随后 report 档 ContextRuntime.build 召回该偏好（report_preferences 链路闭环）。"""
    marker = f"uipref{uuid.uuid4().hex[:6]}"
    user_id = 1  # 本地 dev 库 admin（_chat 直调只要求 user_id int）
    sid = f"s-p165-{uuid.uuid4().hex[:8]}"

    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    class _FakeEmbedder:
        async def embed_or_none(self, text: str):
            v = [0.0001] * int(os.getenv("EMBEDDING_DIM", "1536"))
            for i, kw in enumerate(["销售", "柱状图", "报告", "2024"]):
                if kw in text:
                    v[8 + i] = 1.0
            return v

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())

    # 清场（避免历史数据干扰断言；只清本测试专属文本）
    conn = _conn()
    conn.autocommit = True
    conn.cursor().execute(
        "DELETE FROM memory.semantic_entry WHERE user_id=%s AND content LIKE %s",
        (user_id, f"%{marker}%"),
    )
    conn.close()

    def body():
        return _drive_chat(f"以后报告都用柱状图 {marker}", user_id, sid)

    events = _run(body())
    evt_map = [(e["event"], json.loads(e["data"] or "{}")) for e in events]
    assert ("phase", {"phase": "idle"}) in evt_map, f"应返回 idle 轻响应: {evt_map}"
    report_evt = next(d for e, d in evt_map if e == "report")
    assert "已记住" in report_evt["answer"]["text"]
    assert not any(e == "error" for e, _ in evt_map)

    # DB 行：active + stable_preference + high（§五 契约）
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT memory_type, status, confidence, scope FROM memory.semantic_entry "
        "WHERE user_id=%s AND content LIKE %s",
        (user_id, f"%{marker}%"),
    )
    rows = cur.fetchall()
    conn.close()
    assert rows, "UI 偏好句未写入记忆行"
    assert all(r[1] == "active" and r[2] == "high"
               and r[0] == "stable_preference" and r[3] == "user" for r in rows), (
        f"行属性不符 §五: {rows}"
    )

    # report 档召回闭环：preference 进 recall_items（生产路径产出的 active 行可见）
    def recall():
        async def _go():
            from app.context.runtime import ContextRuntime

            return await ContextRuntime().build(
                session_id=f"s-recall-{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                query=f"{marker} 生成各区域销售报告",
                agent="report_plan_analysis",
                state_dict={"user_id": user_id},
            )

        return _run(_go())

    bundle = recall()
    joined = " ".join(it["raw_text"] for it in bundle["recall_items"])
    assert marker in joined, f"UI 写入的偏好应被 report 档召回: {bundle['recall_items']}"
