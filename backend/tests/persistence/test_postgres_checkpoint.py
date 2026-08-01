"""PostgresSaver 持久化测试。

核心价值证明：checkpoint 落 PG 后能跨实例（模拟进程重启）读回——这是
MemorySaver（进程内 dict）做不到的。沿用本目录 sync + asyncio.run 的可靠模式。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

pytestmark = pytest.mark.persistence


def _run(coro):
    # psycopg3 的 async 模式在 Windows 默认的 ProactorEventLoop 上无法运行，
    # 需切到 Selector 策略。生产/Docker 是 Linux（默认即 Selector 类循环），
    # 本地 dev 走 MemorySaver 不碰 psycopg3——故此适配仅测试层需要。
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def test_checkpoint_persists_across_saver_instances():
    from typing import TypedDict

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, StateGraph
    from psycopg_pool import AsyncConnectionPool

    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")

    class S(TypedDict, total=False):
        x: int

    def inc(state: S) -> dict:
        return {"x": state.get("x", 0) + 1}

    def _build(saver):
        g = StateGraph(S)
        g.add_node("inc", inc)
        g.set_entry_point("inc")
        g.add_edge("inc", END)
        return g.compile(checkpointer=saver)

    thread_id = f"ckpt-test-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    async def body():
        # 实例 1：写入并关闭（模拟进程结束）
        pool1 = AsyncConnectionPool(conninfo=url, open=False, kwargs={"autocommit": True})
        await pool1.open()
        saver1 = AsyncPostgresSaver(pool1)
        await saver1.setup()
        await _build(saver1).ainvoke({"x": 41}, config)
        await pool1.close()

        # 实例 2：全新 pool + saver（模拟重启后），按同 thread_id 读回
        pool2 = AsyncConnectionPool(conninfo=url, open=False, kwargs={"autocommit": True})
        await pool2.open()
        saver2 = AsyncPostgresSaver(pool2)
        snap = await _build(saver2).aget_state(config)
        await pool2.close()
        return snap

    try:
        snap = _run(body())
        assert snap is not None, "checkpoint 未跨实例持久化"
        assert snap.values.get("x") == 42
    finally:
        _cleanup(thread_id)


def _cleanup(thread_id: str):
    """best-effort 清掉本次测试写入的 checkpoint 行，避免污染开发库。"""
    import psycopg

    url = os.getenv("DATABASE_URL")
    if not url:
        return
    try:
        with psycopg.connect(url) as conn:
            for tbl in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE thread_id = %s", (thread_id,))
                except Exception:
                    conn.rollback()
    except Exception:
        pass
