"""P16 ①/⑧：Report 偏好行为测试——「记忆召回 → Report 行为」确定性闭环 + ON/OFF 对照。

memory-architecture §二(2)「报告生成时召回图表偏好」+ §三 Report = Semantic ✅ Preference：
preference 经 ContextRuntime 真召回（真 PG 检索 + fake embedder）→ report_graph._plan_analysis
（P16 接线）→ 确定性 apply_chart_preference → chart_config。

- ① ON：库里 stable_preference「柱状图」→ 6 行区域数据（chart_advisor 默认 pie）被偏好
  override 为 bar（dimensions 键同步归一 category/value → x/y）；
- ⑧ OFF 对照：同 query_result、无偏好行 → chart_advisor 默认 pie（偏好确实改变了行为，
  不是 mock 自说自话——chart_advisor 与偏好应用都是真实现）。

report_graph 的 LLM 调用（report_plan kind）monkeypatch 固定返回 chart_advisor step——
LLM 选步骤不变，行为差异只由 memory 注入引起。
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from app.infra.memory.memory_manager import MemoryManager

pytestmark = pytest.mark.persistence

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class _FakeEmbedder:
    """确定性伪向量（test_vector_recall_pg 同款）：基准 0.01 + v[0]=0.5，
    text 含 'bucket' 时 v[5]=1.0——同桶互为最近邻。"""

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
        (f"p16-{uuid.uuid4().hex[:12]}",),
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


# 6 行区域数据 → chart_advisor（≤8 行）默认 pie
_QR = {
    "sql": "SELECT region, amount FROM fact_sales GROUP BY region",
    "columns": [{"name": "region"}, {"name": "amount"}],
    "rows": [
        {"region": "华东", "amount": 200},
        {"region": "华南", "amount": 180},
        {"region": "华北", "amount": 160},
        {"region": "西南", "amount": 120},
        {"region": "东北", "amount": 100},
        {"region": "西北", "amount": 90},
    ],
    "row_count": 6,
    "status": "SUCCESS",
}

_QUERY = "统计2024年各区域销售额"


def _report_state(user_id: int) -> dict:
    return {
        "query_result": _QR,
        "user_query": _QUERY,
        "chart_config": {},
        "insight": "",
        "report_spec": None,
        "assemble_plan": [],
        "assemble_step_idx": 0,
        "assemble_results": [],
        "trace_id": "p16-1",
        "session_id": f"sess-p16-{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
    }


def _patch_report_llm(monkeypatch):
    """report_plan LLM 固定选 chart_advisor 步骤——行为差异只由 memory 引起。"""
    from app.agent import report_graph as rg

    monkeypatch.setattr(
        rg, "call_llm",
        lambda prompt, **kw: json.dumps(
            {"steps": [{"tool": "chart_advisor", "args": {}, "description": "推荐图表"}]},
            ensure_ascii=False,
        ),
    )


async def _run_report(state: dict) -> dict:
    from app.agent.report_graph import build_report_graph
    return await build_report_graph().ainvoke(state)


def _run(coro):
    """项目 persistence 惯例：sync 测试内包独立事件循环 + pool init/close（vector codec）。"""
    from app.infra.db.postgres import close_pool, init_pool

    async def _body():
        await init_pool()  # init= 注册 vector codec（每连接）
        try:
            return await coro
        finally:
            await close_pool()

    import asyncio
    return asyncio.run(_body())


def test_preference_bar_overrides_default_pie(monkeypatch):
    """① ON：用户偏好柱状图 → chart_config 从默认 pie 变为 bar（含 dims 归一）。"""
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())
    _patch_report_llm(monkeypatch)

    uid = _make_user()
    try:
        async def body():
            await MemoryManager().remember_preference(
                user_id=str(uid),
                content="bucket 报告图表以后都用柱状图",
                memory_type="stable_preference",
                importance=0.8, source="user_turn",
                status="active", confidence="high",
            )
            return await _run_report(_report_state(uid))

        out = _run(body())
        assert out["chart_config"]["type"] == "bar", (
            f"偏好未生效: {out['chart_config']} —— recall/接线断了?"
        )
        assert out["chart_config"]["config"]["dimensions"] == {"x": "region", "y": "amount"}
    finally:
        _cleanup(uid)


def test_without_preference_keeps_advisor_default_pie(monkeypatch):
    """⑧ OFF 对照：同数据同 query、无偏好行 → chart_advisor 默认 pie（行为差异由 memory 引起）。"""
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())
    _patch_report_llm(monkeypatch)

    uid = _make_user()
    try:
        out = _run(_run_report(_report_state(uid)))
        assert out["chart_config"]["type"] == "pie", (
            f"无偏好时应保持 chart_advisor 默认: {out['chart_config']}"
        )
    finally:
        _cleanup(uid)


def test_preference_table_downgrades_to_table(monkeypatch):
    """偏好表格 → 报告无可视化组件，纯表格（词表内类型偏好同样经确定性通道生效）。"""
    from app.infra.memory import query_memory as qm_mod
    from app.infra.memory import user_memory as um_mod

    monkeypatch.setattr(um_mod, "get_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(qm_mod, "get_embedder", lambda: _FakeEmbedder())
    _patch_report_llm(monkeypatch)

    uid = _make_user()
    try:
        async def body():
            await MemoryManager().remember_preference(
                user_id=str(uid),
                content="bucket 报告只要表格不要图表",
                memory_type="stable_preference",
                importance=0.8, source="user_turn",
                status="active", confidence="high",
            )
            return await _run_report(_report_state(uid))

        out = _run(body())
        assert out["chart_config"]["type"] == "table"
        spec = out["report_spec"]
        assert not spec.components, "偏好表格 → 不应有可视化组件"
        assert spec.table is not None and spec.table.columns == ["region", "amount"]
    finally:
        _cleanup(uid)
