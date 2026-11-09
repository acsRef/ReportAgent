"""长期记忆库行为（真 PG + fake embedder）。

plan `2026-11-09-long-term-memory-table`：`memory.long_term_memory` 是**长期库**，
`memory.semantic_entry` 是**工作区**（候选队列 + 会话级临时口径）。本文件钉住：

- 槽位唯一 → 换图/换口径是 UPSERT 覆盖（`revision+1`、旧值留 `previous_content`），不是两行；
- 同内容重申 → 一行、`revision` 不变、`evidence_count` 累加；
- 召回合并两库（长期库 + 工作区的会话级），且 `record_access` 各自记各自的热度；
- 会话级临时口径仍然只在原会话可见（D4 的行为不变）。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class _FakeEmbedder:
    async def embed_or_none(self, text: str):
        v = [0.01] * _DIM
        v[0] = 0.5
        return v


def _conn():
    import psycopg2

    return psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")
    )


def _make_user() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"ltm-{uuid.uuid4().hex[:10]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int) -> None:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.long_term_memory WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _lt(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT memory_key, content, revision, previous_content, confidence, "
        "evidence_count, access_count FROM memory.long_term_memory "
        "WHERE user_id=%s ORDER BY id",
        (user_id,),
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _patch_embedders(monkeypatch) -> None:
    from app.infra.memory import long_term as lt_mod
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    for mod in (lt_mod, qm_mod, um_mod):
        monkeypatch.setattr(mod, "get_embedder", lambda: _FakeEmbedder())


# ------------------------------------------------------------ 槽位唯一 / 覆盖


def test_same_slot_overwrites_and_keeps_previous(monkeypatch):
    """柱状图 → 饼图：同一槽位被覆盖，旧值得以回溯，revision 递增。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后都用柱状图显示")
            await remember_explicit_preference(uid, "以后都用饼图显示")

        _run(body())
        rows = _lt(uid)
        assert len(rows) == 1, f"同槽位只可能一行: {rows}"
        assert rows[0]["memory_key"] == "pref:chart"
        assert "饼图" in rows[0]["content"]
        assert rows[0]["revision"] == 2
        assert "柱状图" in (rows[0]["previous_content"] or "")
    finally:
        _cleanup(uid)


def test_restating_same_content_does_not_bump_revision(monkeypatch):
    """重申同一句：一行、revision 不变、evidence_count 累加。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后都用柱状图显示")
            await remember_explicit_preference(uid, "以后都用柱状图显示")

        _run(body())
        rows = _lt(uid)
        assert len(rows) == 1
        assert rows[0]["revision"] == 1, "同内容不是改口径"
        assert rows[0]["evidence_count"] >= 2
    finally:
        _cleanup(uid)


def test_different_slots_coexist(monkeypatch):
    """图表与粒度是不同槽位：互不覆盖。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后都用饼图")
            await remember_explicit_preference(uid, "以后按月的粒度来显示")

        _run(body())
        keys = {r["memory_key"] for r in _lt(uid)}
        assert keys == {"pref:chart", "pref:granularity"}
    finally:
        _cleanup(uid)


# ------------------------------------------------------------------- 召回合并


def test_recall_merges_long_term_and_session_scoped(monkeypatch):
    """召回同时看到长期库与会话级临时口径；别的会话看不到临时的。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference
        from app.infra.memory.user_memory import UserMemory

        async def body():
            await remember_explicit_preference(uid, "以后都用饼图")
            await remember_explicit_preference(uid, "这次先按周看", session_id="sess-A")
            um = UserMemory()
            same = await um.search(str(uid), "饼图 按周", top_k=5, session_id="sess-A")
            other = await um.search(str(uid), "饼图 按周", top_k=5, session_id="sess-B")
            return same, other

        same, other = _run(body())
        same_text = " ".join(m.content for m in same)
        other_text = " ".join(m.content for m in other)
        assert "饼图" in same_text, same
        assert "按周" in same_text, "会话级在同一会话里必须召回"
        assert "饼图" in other_text, "长期记忆跨会话可见"
        assert "按周" not in other_text, "会话级不得跨会话泄漏"
        # store 标记正确（record_access 要靠它选表）
        assert {m.store for m in same} <= {"long_term", "semantic"}
    finally:
        _cleanup(uid)


def test_record_access_updates_the_right_table(monkeypatch):
    """召回后热度记到各自表上——否则长期记忆的 LFU/LRU 永远不动。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference
        from app.infra.memory.user_memory import UserMemory

        async def body():
            await remember_explicit_preference(uid, "以后都用饼图")
            await UserMemory().search(str(uid), "饼图", top_k=3, session_id="s")

        _run(body())
        assert _lt(uid)[0]["access_count"] >= 1, "长期库的 access_count 应被召回累加"
    finally:
        _cleanup(uid)


# --------------------------------------------------------------------- 防腐


def test_expire_stale_removes_cold_long_term_rows(monkeypatch):
    """长期≠永远：太久没更新/召回的行由批处理清掉（默认 180 天，这里用 0 天验证路径）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference
        from app.infra.memory.memory_manager import MemoryManager

        async def body():
            await remember_explicit_preference(uid, "以后都用饼图")
            return await MemoryManager().expire_long_term(uid, days=0)

        removed = _run(body())
        assert removed == 1
        assert _lt(uid) == []
    finally:
        _cleanup(uid)
