"""记忆离线固化行为（真 PG + fake embedder）。

plan `2026-11-09-memory-admission-and-consolidation`：判定不在请求里做。
热路径只写「显式且一眼可判」的（长期标记→user / 会话标记→session+TTL），
其余落 `candidate`；这里测的是**批处理**：候选晋级、到期淘汰、同维度收敛、
幂等重跑、dry-run 不落库、无标记继续留队。
"""
from __future__ import annotations

import asyncio
import datetime
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
        (f"cons-{uuid.uuid4().hex[:12]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int) -> None:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM memory.consolidation_cursor WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM memory.consolidation_run WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _rows(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, content, memory_type, status, scope, session_id, expires_at, promoted_at "
        "FROM memory.semantic_entry WHERE user_id=%s ORDER BY id",
        (user_id,),
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def _runs(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT considered, promoted, expired, superseded, skipped, errors, dry_run "
        "FROM memory.consolidation_run WHERE user_id=%s ORDER BY id",
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
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())


def _seed_candidate(uid: int, content: str, *, session_id: str | None = None) -> None:
    """直接写一条 candidate（模拟 LLM 抽取 / 无作用域标记的落库形态）。"""
    from app.infra.memory.memory_manager import MemoryManager

    _run(
        MemoryManager().remember_preference(
            user_id=str(uid), content=content, memory_type="insight",
            importance=0.5, source="test_seed",
            scope="user", status="candidate", confidence="high",
            session_id=session_id,
        )
    )


# --------------------------------------------------------------------- 晋级


def test_candidate_with_long_marker_promotes_to_user_scoped(monkeypatch):
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        _seed_candidate(uid, "以后都用柱状图显示")
        from app.memory.consolidation import consolidate

        summary = _run(consolidate(uid))
        assert summary["promoted"] == 1, summary
        row = _rows(uid)[0]
        assert row["status"] == "active"
        assert row["scope"] == "user"
        assert row["memory_type"] == "stable_preference"
        assert row["expires_at"] is None
        assert row["promoted_at"] is not None
    finally:
        _cleanup(uid)


def test_candidate_with_session_marker_promotes_to_session_with_ttl(monkeypatch):
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        _seed_candidate(uid, "这次先按周看", session_id="sess-cons-1")
        from app.memory.consolidation import consolidate

        summary = _run(consolidate(uid))
        assert summary["promoted"] == 1, summary
        row = _rows(uid)[0]
        assert row["status"] == "active"
        assert row["scope"] == "session"
        assert row["memory_type"] == "temporary_preference"
        assert row["expires_at"] is not None
    finally:
        _cleanup(uid)


def test_candidate_without_scope_marker_stays_in_queue(monkeypatch):
    """§五：推断类不晋级。无作用域标记的候选继续留队，等「跨会话证据」那条路（D6）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        _seed_candidate(uid, "柱状图挺好的")
        from app.memory.consolidation import consolidate

        summary = _run(consolidate(uid))
        assert (summary["promoted"], summary["skipped"]) == (0, 1), summary
        row = _rows(uid)[0]
        assert row["status"] == "candidate"
        assert row["promoted_at"] is None
    finally:
        _cleanup(uid)


# ------------------------------------------------------------- 到期 / 幂等


def test_expired_session_preference_is_retired(monkeypatch):
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.infra.memory.memory_manager import MemoryManager

        _run(
            MemoryManager().remember_preference(
                user_id=str(uid), content="这次先按周看", memory_type="temporary_preference",
                importance=0.8, source="user_turn", scope="session", status="active",
                confidence="high", session_id="sess-old",
                expires_at=datetime.datetime.now() - datetime.timedelta(hours=1),
            )
        )
        from app.memory.consolidation import consolidate

        summary = _run(consolidate(uid))
        assert summary["expired"] == 1, summary
        assert _rows(uid)[0]["status"] == "expired"
    finally:
        _cleanup(uid)


def test_second_run_is_idempotent(monkeypatch):
    """同一批候选跑两次：第二次 considered=0 / promoted=0，且不新增行。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        _seed_candidate(uid, "以后都用饼图显示")
        from app.memory.consolidation import consolidate

        first = _run(consolidate(uid))
        second = _run(consolidate(uid))
        assert first["promoted"] == 1
        assert (second["considered"], second["promoted"]) == (0, 0), second
        assert len(_rows(uid)) == 1, "重跑不得复制行"
        # 两次都记账（运维要能看出「跑过、没活干」）
        assert len(_runs(uid)) == 2
    finally:
        _cleanup(uid)


# ------------------------------------------------------------------ dry-run


def test_dry_run_reports_but_writes_nothing(monkeypatch):
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        _seed_candidate(uid, "以后都用表格显示")
        from app.memory.consolidation import consolidate

        summary = _run(consolidate(uid, dry_run=True))
        assert summary["promoted"] == 1, summary
        row = _rows(uid)[0]
        assert row["status"] == "candidate", "dry-run 不得落库"
        assert any(a.get("reason") == "long-term-marker" for a in summary["actions"])
        assert _runs(uid)[0]["dry_run"] is True
    finally:
        _cleanup(uid)


# --------------------------------------------------------------- 冲突收敛


def test_same_dimension_conflict_keeps_newest_active(monkeypatch):
    """同维度（呈现形态）只能有一条 active：批处理兜住历史脏数据。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.infra.memory.memory_manager import MemoryManager

        for content in ("以后都用柱状图显示", "以后都用饼图显示"):
            _run(
                MemoryManager().remember_preference(
                    user_id=str(uid), content=content, memory_type="stable_preference",
                    importance=0.8, source="seed", scope="user", status="active",
                    confidence="high",
                )
            )
        from app.memory.consolidation import consolidate

        summary = _run(consolidate(uid))
        assert summary["superseded"] >= 1, summary
        active = [r for r in _rows(uid) if r["status"] == "active"]
        assert len(active) == 1, active
        assert "饼图" in active[0]["content"], "应保留最新那条"
    finally:
        _cleanup(uid)


def test_max_items_caps_one_run_and_next_run_continues(monkeypatch):
    """预算上限：一轮只处理 N 条，余下的下一轮接着来（游标不丢活）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        # 三条分属不同维度（呈现 / 粒度 / 无维度），不会在冲突收敛里互相顶掉
        for content in (
            "以后都用柱状图显示 v0",
            "以后按月的粒度来显示 v1",
            "以后默认按净销售额算 v2",
        ):
            _seed_candidate(uid, content)
        from app.memory.consolidation import consolidate

        first = _run(consolidate(uid, max_items=2))
        second = _run(consolidate(uid, max_items=2))
        assert first["promoted"] == 2, first
        assert second["promoted"] == 1, second
        assert all(r["promoted_at"] is not None for r in _rows(uid))
    finally:
        _cleanup(uid)
