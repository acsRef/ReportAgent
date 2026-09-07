"""P16 W2 行为 guardrails：Agent 层「记忆不得污染 / 不可用记忆不得进入」钉子。

repo 层（persistence test_cross_user_isolation 等）已钉 SQL 过滤；本文件补 **Agent 层**
（ContextRuntime.build 产出 + assembled_context）证明过滤贯穿到真实召回结果：

- ② EXECUTION 档 drop preference（P16 D4）：带「图表报告」词 query 触发 semantic 召回时，
  preference 类（stable_preference）不得进入 SQL 上下文——业务事实（insight 类）可以留；
- ⑤ candidate 行：DB 存在但绝不进入 recall / assembled_context；
- ⑥ expired 行（expires_at 已过）：DB 存在但绝不进入 recall / assembled_context；
- ⑦ cross-user：A 的偏好对 B 的召回完全不可见（Agent 层隔离）。

真 PG 检索 + fake embedder（确定性同桶最近邻）；每测试独立 user + 独立 marker，
断言只针对自己的 marker（防串扰）。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.infra.memory.memory_manager import MemoryManager

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class _FakeEmbedder:
    async def embed_or_none(self, text: str):
        v = [0.01] * _DIM
        v[0] = 0.5
        if "bucket" in text:
            v[5] = 1.0
        return v


def _conn():
    import psycopg2
    return psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")


def _make_user() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"p16g-{uuid.uuid4().hex[:12]}",),
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


def _row_count(user_id: int, marker: str) -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*) FROM memory.semantic_entry WHERE user_id=%s AND content LIKE %s",
        (user_id, f"%{marker}%"),
    )
    n = cur.fetchone()[0]
    conn.close()
    return n


def _run(coro):
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_body())


def _patch_embedders(monkeypatch):
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())


async def _build(user_id: int, query: str, agent: str) -> dict:
    from app.context.runtime import ContextRuntime

    return await ContextRuntime().build(
        session_id=f"sess-p16-{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        query=query,
        agent=agent,
        state_dict={"user_id": user_id},
    )


def test_execution_agent_drops_preference_keeps_business_fact(monkeypatch):
    """② EXECUTION 档：图表偏好不进 SQL 上下文；业务事实（insight 类）可留。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    marker_pref = f"bpref{uuid.uuid4().hex[:6]}"
    marker_fact = f"bfact{uuid.uuid4().hex[:6]}"
    try:
        async def body():
            mm = MemoryManager()
            await mm.remember_preference(
                user_id=str(uid), content=f"bucket {marker_pref} 报告图表以后都用柱状图",
                memory_type="stable_preference", importance=0.8, source="user_turn",
                status="active", confidence="high",
            )
            await mm.remember_preference(
                user_id=str(uid), content=f"bucket {marker_fact} GMV 按净销售额口径统计",
                memory_type="insight", importance=0.8, source="user_turn",
                status="active", confidence="high",
            )
            return await _build(
                uid,
                f"生成 2024 年各区域销售额图表报告 {marker_pref}",
                "confirmed_execution_sql_agent",  # resolver → EXECUTION 档
            )

        bundle = _run(body())
        kinds = {(it["source"], it["kind"]) for it in bundle["recall_items"]}
        assert ("memory_preference", "preference") not in kinds, (
            f"EXECUTION 档不得召回 preference: {bundle['recall_items']}"
        )
        assert ("memory_semantic", "semantic") in kinds, "业务事实应可进 SQL 上下文"
        assert marker_pref not in bundle["assembled_context"], (
            "偏好文本不得出现在 SQL assembled_context"
        )
        assert marker_fact in bundle["assembled_context"]
    finally:
        _cleanup(uid)


def test_candidate_row_never_enters_recall_or_context(monkeypatch):
    """⑤ LLM-inferred candidate：DB 行存在但 Agent 召回与注入均为空。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    marker = f"bcand{uuid.uuid4().hex[:6]}"
    try:
        async def body():
            mm = MemoryManager()
            await mm.remember_preference(
                user_id=str(uid), content=f"bucket {marker} 用户可能喜欢按月汇总",
                memory_type="insight", importance=0.3, source="llm_inferred",
                status="candidate", confidence="low",
            )
            return await _build(uid, f"{marker} 统计销售额", "report_plan_analysis")

        bundle = _run(body())
        assert _row_count(uid, marker) == 1, "candidate 行应真实存在于 DB"
        assert marker not in " ".join(it["raw_text"] for it in bundle["recall_items"]), (
            "candidate 不得被 recall（active-only SQL）"
        )
        assert marker not in bundle["assembled_context"]
    finally:
        _cleanup(uid)


def test_expired_row_never_enters_recall_or_context(monkeypatch):
    """⑥ expires_at 已过：DB 行存在（status=active 但过期）→ 召回与注入均为空。

    过期值用 DB 端 `NOW() - interval` 直 UPDATE 制造（naive datetime 经 asyncpg 绑无时区
    列与 NOW() 比较受会话时区影响——DB 端生成避免测试假阳性）。
    """
    _patch_embedders(monkeypatch)
    uid = _make_user()
    marker = f"bexp{uuid.uuid4().hex[:6]}"
    try:
        async def body():
            mm = MemoryManager()
            entry_id = await mm.remember_preference(
                user_id=str(uid), content=f"bucket {marker} 本季度促销月报用表格",
                memory_type="temporary_preference", importance=0.8, source="user_turn",
                status="active", confidence="high",
            )
            conn = _conn()
            conn.autocommit = True
            conn.cursor().execute(
                "UPDATE memory.semantic_entry SET expires_at = NOW() - interval '1 hour' "
                "WHERE id=%s",
                (entry_id,),
            )
            conn.close()
            return await _build(uid, f"{marker} 促销月报", "report_plan_analysis")

        bundle = _run(body())
        assert _row_count(uid, marker) == 1, "expired 行应真实存在于 DB"
        assert marker not in " ".join(it["raw_text"] for it in bundle["recall_items"]), (
            "expired 行不得召回（expires_at <= NOW() 排除）"
        )
        assert marker not in bundle["assembled_context"]
    finally:
        _cleanup(uid)


def test_cross_user_isolation_at_agent_layer(monkeypatch):
    """⑦ A 的偏好对 B 完全不可见：B 同 query、同档位 build → 召回空。"""
    _patch_embedders(monkeypatch)
    uid_a = _make_user()
    uid_b = _make_user()
    marker = f"bcross{uuid.uuid4().hex[:6]}"
    try:
        async def body():
            mm = MemoryManager()
            await mm.remember_preference(
                user_id=str(uid_a), content=f"bucket {marker} 报告都用柱状图",
                memory_type="stable_preference", importance=0.8, source="user_turn",
                status="active", confidence="high",
            )
            # B 没有任何行——同 query 召回必须为空
            return await _build(uid_b, f"{marker} 统计销售额生成报告", "report_plan_analysis")

        bundle = _run(body())
        assert bundle["recall_items"] == [], (
            f"B 不应看到 A 的任何记忆: {bundle['recall_items']}"
        )
        assert marker not in bundle["assembled_context"]
    finally:
        _cleanup(uid_a)
        _cleanup(uid_b)
