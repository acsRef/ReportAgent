"""记忆系统契约一致性（`docs/architecture/memory-architecture.md` §一–§七）。

与既有测试的分工（不重复造）：

- `contracts/test_memory_lifecycle_contract.py`：状态/作用域/置信度**枚举**与「只 active 可召回」；
- `contracts/test_memory_write_pipeline_contract.py`：写管线固定规则（explicit→active、inferred→candidate）；
- `persistence/test_memory_agent_layer_guardrails.py`：档位过滤、candidate/expired 排除、跨用户隔离；
- **本文件**：把契约里「行为承诺」逐条做成带条款号的端到端断言（真 PG + fake embedder），
  并钉住 2026-11-09 静态排查发现的**未兑现项**（`xfail(strict=True)`：修好后会变 XPASS 并失败，
  强制回来更新矩阵，而不是在文档里悄悄烂掉）。

条款编号对应 plan `2026-11-09-memory-system-conformance.md`。
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class _FakeEmbedder:
    """确定性向量：所有文本落同一桶，保证召回由 id/重要性决定，不受语义抖动影响。"""

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
        (f"conf-{uuid.uuid4().hex[:12]}",),
    )
    uid = cur.fetchone()[0]
    conn.close()
    return uid


def _cleanup(user_id: int) -> None:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM memory.semantic_entry WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM memory.query_template WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM memory.query_failure WHERE user_id=%s", (user_id,))
    cur.execute("DELETE FROM app.users WHERE id=%s", (user_id,))
    conn.close()


def _query_memory_rows(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT question, sql_text, success_count, target_metric "
        "FROM memory.query_template WHERE user_id=%s ORDER BY id",
        (user_id,),
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def _semantic_rows(user_id: int, *, status: str | None = None) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    sql = (
        "SELECT id, content, memory_type, status, scope, session_id "
        "FROM memory.semantic_entry WHERE user_id=%s"
    )
    params: list = [user_id]
    if status is not None:
        sql += " AND status=%s"
        params.append(status)
    cur.execute(sql + " ORDER BY id", params)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def _failure_rows(user_id: int) -> list[dict]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT question, failed_sql, failure_category, error_message, retry_count, "
        "trace_id, session_id FROM memory.query_failure WHERE user_id=%s ORDER BY id",
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


# ---------------------------------------------------------------- C1/C2：§四


def test_c1_writeback_predicate_only_success_non_adjust():
    """§四：只有「校验通过 + 执行成功」的这一轮才成为经验。

    EMPTY 虽执行成功但零行（当成经验会持续推荐查不到数据的口径）；FAILED 是陷阱不是经验；
    adjust 的 user_query 是补充语，存成 question 只会污染检索。
    """
    from app.agent.confirmed_execution_graph import _should_writeback_query_memory

    assert _should_writeback_query_memory("SUCCESS", is_adjust=False) is True
    assert _should_writeback_query_memory("EMPTY", is_adjust=False) is False
    assert _should_writeback_query_memory("FAILED", is_adjust=False) is False
    assert _should_writeback_query_memory("SUCCESS", is_adjust=True) is False


def test_c1_successful_run_writes_query_memory(monkeypatch):
    """§一/§四：成功的一轮 → `memory.query_template` 真的多一行（这是 D1 的回归钉）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.agent.confirmed_execution_graph import _remember_successful_query

        state = {
            "user_id": uid,
            "user_query": "2024年各区域销售额排名",
            "sql": "SELECT region, SUM(order_amount) FROM fact_orders GROUP BY region",
            "requirement_card": None,
        }
        _run(_remember_successful_query(state))

        rows = _query_memory_rows(uid)
        assert len(rows) == 1, rows
        assert rows[0]["question"] == "2024年各区域销售额排名"
        assert rows[0]["sql_text"].startswith("SELECT region")
    finally:
        _cleanup(uid)


def test_c9_same_sql_twice_accumulates_not_duplicates(monkeypatch):
    """§一 Query Memory 去重：同一 (sql, user) 二次成功应累加 success_count，不产生第二行。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.agent.confirmed_execution_graph import _remember_successful_query

        state = {
            "user_id": uid,
            "user_query": "2024年各区域销售额排名",
            "sql": "SELECT region, SUM(order_amount) FROM fact_orders GROUP BY region",
            "requirement_card": None,
        }
        _run(_remember_successful_query(state))
        _run(_remember_successful_query(state))

        rows = _query_memory_rows(uid)
        assert len(rows) == 1, f"同一 SQL 不应产生第二行: {rows}"
        assert rows[0]["success_count"] >= 2, rows
    finally:
        _cleanup(uid)


def test_c2_empty_query_text_is_not_written(monkeypatch):
    """§四 边界：没有 SQL（或没有问题原文）时不写空经验。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.agent.confirmed_execution_graph import _remember_successful_query

        _run(_remember_successful_query({"user_id": uid, "user_query": "", "sql": "SELECT 1"}))
        _run(_remember_successful_query({"user_id": uid, "user_query": "查一下", "sql": ""}))
        assert _query_memory_rows(uid) == []
    finally:
        _cleanup(uid)


def test_c2_confirm_flow_uses_card_summary_when_user_query_empty(monkeypatch):
    """/confirm 流的 `user_query` 是空串——经验的问题文本必须退回需求卡摘要。

    2026-11-09 真链路实测：e2e happy-path 跑完 `memory.query_template` 仍 0 行，
    原因就是这里（只读 `state["user_query"]` 会直接 return）。
    """
    _patch_embedders(monkeypatch)
    uid = _make_user()

    class _Card:
        summary = "2024年各区域销售额排名"
        target_metrics = ["销售额"]

    try:
        from app.agent.confirmed_execution_graph import _remember_successful_query

        state = {
            "user_id": uid,
            "user_query": "",  # /confirm 入口的真实形态
            "sql": "SELECT region, SUM(order_amount) FROM fact_orders GROUP BY region",
            "requirement_card": _Card(),
        }
        _run(_remember_successful_query(state))

        rows = _query_memory_rows(uid)
        assert len(rows) == 1, rows
        assert rows[0]["question"] == "2024年各区域销售额排名"
        assert rows[0]["target_metric"] == "销售额"
    finally:
        _cleanup(uid)


# ------------------------------------------------------------------- C3：§六


def test_c3_restating_same_preference_keeps_single_active(monkeypatch):
    """§六：重申同一偏好不得产生两条 active（旧的行保持唯一）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后都用柱状图显示")
            await remember_explicit_preference(uid, "以后都用柱状图显示")

        _run(body())
        rows = _semantic_rows(uid, status="active")
        assert len(rows) == 1, f"同内容重申后 active 行应唯一: {rows}"
    finally:
        _cleanup(uid)


# --------------------------------------------- C4：§六 同维度冲突 supersede


def test_c4_conflicting_preference_supersedes_old_active(monkeypatch):
    """§六：柱状图 → 饼图属同一维度（呈现形态），旧的 active 必须变 superseded。

    2026-11-09（plan memory-system-conformance D3）：此前只做同内容 supersede，
    两条 active 并存 → 召回时给出互相矛盾的偏好。现在由
    `app/memory/preferences.preference_dimension` 做确定性维度判定。
    """
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            await remember_explicit_preference(uid, "以后都用柱状图显示")
            await remember_explicit_preference(uid, "以后都用饼图显示")

        _run(body())
        active = _semantic_rows(uid, status="active")
        assert len(active) == 1, f"§六：新旧偏好冲突时旧应 superseded，实际 active={active}"
        assert "饼图" in active[0]["content"], active
        superseded = _semantic_rows(uid, status="superseded")
        assert any("柱状图" in r["content"] for r in superseded), superseded
    finally:
        _cleanup(uid)


def test_c4_cross_dimension_preference_is_kept(monkeypatch):
    """§六 反例：不同维度不该互相顶掉（「饼图」不该把「按月看」废掉）。"""
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.memory.manager import remember_explicit_preference

        async def body():
            # 「按月」属粒度维度，「饼图」属呈现维度——两者不冲突，都该留着
            await remember_explicit_preference(uid, "以后按月的粒度来显示")
            await remember_explicit_preference(uid, "以后都用饼图显示")

        _run(body())
        active = [r["content"] for r in _semantic_rows(uid, status="active")]
        assert len(active) == 2, f"跨维度偏好不应被 supersede: {active}"
    finally:
        _cleanup(uid)


# ------------------------------------------------ C5：§五 session 作用域


def test_c5_session_scoped_preference_not_recalled_elsewhere(monkeypatch):
    """§五：session 级 temporary_preference 只在原会话（或显式给出该 session）可召回。

    2026-11-09（plan D4）：此前召回过滤没有 session 维度，A 会话的临时口径会串到 B 会话。
    """
    _patch_embedders(monkeypatch)
    uid = _make_user()
    try:
        from app.infra.memory.user_memory import UserMemory

        async def body():
            um = UserMemory()
            await um.save(
                user_id=str(uid),
                content="这次先按周看，下次再说",
                memory_type="temporary_preference",
                importance_score=0.9,
                source="user_turn",
                scope="session",
                status="active",
                confidence="high",
                session_id="sess-A",
            )
            same = await um.search(str(uid), "按周看", top_k=5, session_id="sess-A")
            other = await um.search(str(uid), "按周看", top_k=5, session_id="sess-B")
            none = await um.search(str(uid), "按周看", top_k=5)  # 无会话上下文
            return same, other, none

        same, other, none = _run(body())
        assert any("下次再说" in i.content for i in same), "原会话应能召回"
        assert not any("下次再说" in i.content for i in other), "别的会话不得召回"
        assert not any("下次再说" in i.content for i in none), "无 session 时一律不召回"
    finally:
        _cleanup(uid)


# -------------------------------------------- C6：§四 失败明细（D2）


def test_c6_failed_run_records_failure_detail(monkeypatch):
    """§四：失败 SQL 不进成功经验，但明细（kind / sql / 重试次数）必须可查。

    2026-11-09（plan D2）：`memory.query_template` 里根本没有这些列，
    `record_failure` 只能对已存在的成功行 +1 → 失败无处可落。现单开 `memory.query_failure`。
    """
    _patch_embedders(monkeypatch)
    uid = _make_user()

    class _Card:
        summary = "2025年各区域销售额"
        target_metrics = ["销售额"]

    class _Err:
        def model_dump(self):
            return {"code": "QUERY_FAILED", "kind": "object_not_found", "message": "column does not exist"}

    try:
        from app.agent.confirmed_execution_graph import _remember_failed_query

        state = {
            "user_id": uid,
            "user_query": "",
            "session_id": "sess-fail",
            "trace_id": "trace-fail",
            "sql": "SELECT nonexistent FROM fact_orders",
            "error": _Err(),
            "requirement_card": _Card(),
            "retry_counters": {"sql_generation": 2, "plan": 0},
        }
        _run(_remember_failed_query(state))

        rows = _failure_rows(uid)
        assert len(rows) == 1, rows
        assert rows[0]["question"] == "2025年各区域销售额"
        assert rows[0]["failure_category"] == "object_not_found"
        # retry_count 来自子图透传的 `retry_counters.sql_generation`（父图 state 已声明该 channel；
        # 此前未声明 → LangGraph 直接丢 → 永远记 0）。
        assert rows[0]["retry_count"] == 2
        assert "nonexistent" in (rows[0]["failed_sql"] or "")
        assert rows[0]["trace_id"] == "trace-fail"
        # 关键：失败**不**进成功经验
        assert _query_memory_rows(uid) == []
    finally:
        _cleanup(uid)
