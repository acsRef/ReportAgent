"""F7 修复钉子：supersede_stable_preferences SQL 端到端测试（真 PG）。

P4b §六 supersede 把 user 既存 active stable_preference（同 content）置 superseded，
避免显式重申时双 active 互矛盾。钉住：
  1. 写 2 条 active stable_preference + 1 条 candidate insight
  2. 调 supersede_stable_preferences 重申其中 1 条
  3. 验证：1 条 superseded（content 命中）/ 1 条 active 保留（content 不同）/
     1 条 candidate 不动（status!='active' 不被 supersede 影响）

Fixture 模式：psycopg2 直连 + monkeypatch embedder（避免真实 embedding API）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.infra.memory.memory_manager import MemoryManager

pytestmark = pytest.mark.persistence


def _conn():
    import psycopg2
    return psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")


def _make_user_id() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"supersede-{uuid.uuid4().hex[:12]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int):
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _run(coro):
    from app.infra.db.postgres import init_pool, close_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def test_supersede_moves_active_to_superseded_and_preserves_candidate(monkeypatch):
    """supersede SQL：同 content 的 active stable_preference → superseded；
    不同 content 的 active 保留；candidate 行（status!='active'）不动。"""
    from app.infra.memory import user_memory as um_mod

    class _FakeEmbedder:
        async def embed_or_none(self, text):
            return None  # INSERT 不需要真实向量；保留 NULL 让 intent_embedding 落 NULL

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())

    user_id = _make_user_id()
    try:
        async def body():
            mm = MemoryManager()
            # 1. 写 2 条 active stable_preference + 1 条 candidate insight
            # 注：user_id 传 int——user_memory.save 内部 SQL 透传给 PG，PG 列 INT
            # 推断 $1=INT，传 str 会报 DataError（asyncpg 不自动转换）。
            await mm.remember_preference(
                user_id=user_id, content="以后用图表显示",
                memory_type="stable_preference", status="active", confidence="high",
            )
            await mm.remember_preference(
                user_id=user_id, content="用表格显示",
                memory_type="stable_preference", status="active", confidence="high",
            )
            await mm.remember_preference(
                user_id=user_id, content="some inference",
                memory_type="insight", status="candidate", confidence="low",
            )
            # 2. 调 supersede 重申 "以后用图表显示"（同 content）
            return await mm.supersede_stable_preference(
                user_id, "以后用图表显示",
            )

        superseded_count = _run(body())
        assert superseded_count == 1, f"expected 1 superseded, got {superseded_count}"

        # 3. 验证三行状态
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT content, memory_type, status FROM memory.semantic_entry "
            "WHERE user_id=%s ORDER BY id",
            (user_id,),
        )
        rows = cur.fetchall()
        conn.close()
        rows_by_content = {r[0]: (r[1], r[2]) for r in rows}
        # 同 content → status 改为 superseded
        assert rows_by_content["以后用图表显示"] == ("stable_preference", "superseded")
        # 不同 content → active 保留
        assert rows_by_content["用表格显示"] == ("stable_preference", "active")
        # candidate → 不动（SQL WHERE status='active' 不命中 candidate 行）
        assert rows_by_content["some inference"] == ("insight", "candidate")
    finally:
        _cleanup(user_id)