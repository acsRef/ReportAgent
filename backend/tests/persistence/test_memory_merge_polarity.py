"""极性槽位与语义归并的持久化行为（真 PG + fake embedder）。

plan `2026-11-10-semantic-memory-merge`：

- **D3 极性槽**：「以后不要用柱状图」不能再把「以后都用饼图」顶掉——
  否定句落 `pref:chart:exclude`，正向偏好留在 `pref:chart`；
- **D2 归并**：同义说法（结构一致）把证据累加到既有长期条目，不新建行；
  灰区（无结构信号 + 余弦高）只进 `merge_candidate`，长期库不变。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class _FakeEmbedder:
    """所有文本同向 → 余弦 ≈ 1.0；用来稳定触发「灰区」路径。"""

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
        (f"pol-{uuid.uuid4().hex[:10]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int) -> None:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    for table in (
        "memory.semantic_entry",
        "memory.long_term_memory",
        "memory.merge_candidate",
        "memory.consolidation_run",
        "memory.consolidation_cursor",
    ):
        cur.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _lt(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT memory_key, content, evidence_count, revision FROM memory.long_term_memory "
        "WHERE user_id=%s ORDER BY id",
        (user_id,),
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def _reviews(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT candidate_content, target_content, similarity, reason "
        "FROM memory.merge_candidate WHERE user_id=%s ORDER BY id",
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


# ------------------------------------------------------------- D3 极性槽


def test_negated_preference_does_not_overwrite_positive(monkeypatch):
    """正向偏好与否定约束各占一槽——不再互相覆盖（此前会丢正向偏好）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后都用饼图")
            await remember_explicit_preference(uid, "以后不要用柱状图")

        _run(body())
        rows = {r["memory_key"]: r["content"] for r in _lt(uid)}
        assert rows["pref:chart"] == "以后都用饼图", rows
        assert rows["pref:chart:exclude"] == "以后不要用柱状图", rows
        assert len(rows) == 2, "极性与正向必须是两个槽"
    finally:
        _cleanup(uid)


def test_positive_still_wins_when_applied(monkeypatch):
    """两条并存时，应用层仍得出「饼图」：正面偏好生效、否定只作排除。"""
    from app.report.preference import apply_chart_preference

    chart = apply_chart_preference(
        {"type": "bar", "config": {"dimensions": {"x": "region", "y": "v"}, "data": []}},
        ["以后都用饼图", "以后不要用柱状图"],
    )
    assert chart["type"] == "pie"


def test_negated_granularity_has_its_own_slot(monkeypatch):
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后按月的粒度来显示")
            await remember_explicit_preference(uid, "以后不要按月的粒度来显示")

        _run(body())
        keys = {r["memory_key"] for r in _lt(uid)}
        assert keys == {"pref:granularity", "pref:granularity:exclude"}
    finally:
        _cleanup(uid)


# --------------------------------------------------- D2 同义说法累加证据


def test_paraphrase_accumulates_evidence_without_new_row(monkeypatch):
    """换一种说法 → 判定器说 merge → 证据累加到既有条目，不新建行。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_inferred_facts
        from app.memory.consolidation import consolidate

        async def body():
            for _ in range(3):
                await remember_inferred_facts(uid, ["净销售额是扣退款后的口径"])
            await consolidate(uid, min_evidence=3)          # 先晋级进长期库
            await remember_inferred_facts(uid, ["净销售额等于销售额减掉退款"])
            first = await consolidate(uid, min_evidence=99)  # 关掉证据升级，只看归并
            return first

        summary = _run(body())
        assert summary["merged"] == 1, summary
        rows = _lt(uid)
        assert len(rows) == 1, f"同义说法不应新建行: {rows}"
        assert rows[0]["evidence_count"] == 2, rows
    finally:
        _cleanup(uid)


# ----------------------------------------------------------- 灰区复审队列


def test_gray_zone_is_recorded_not_merged(monkeypatch):
    """无结构信号 + 余弦高 → 记 merge_candidate，长期库不变（不自动合并）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference
        from app.memory.consolidation import consolidate

        async def body():
            # 先放一条长期记忆（无指标/无维度原子值）
            await remember_explicit_preference(uid, "以后报告都用简洁的写法")
            before = _lt(uid)
            # 再造一条无明显结构信号的候选
            from app.infra.memory.memory_manager import MemoryManager

            await MemoryManager().remember_preference(
                user_id=str(uid), content="报告写得简短一些", memory_type="insight",
                importance=0.5, source="test", scope="user", status="candidate",
                confidence="low",
            )
            summary = await consolidate(uid, min_evidence=99)
            return before, summary

        before, summary = _run(body())
        assert summary["reviewed"] == 1, summary
        assert summary["merged"] == 0
        reviews = _reviews(uid)
        assert reviews and reviews[0]["candidate_content"] == "报告写得简短一些", reviews
        after = _lt(uid)
        assert len(after) == len(before), "复审不得改动长期库"
        assert all(r["evidence_count"] == 1 for r in after)
    finally:
        _cleanup(uid)
