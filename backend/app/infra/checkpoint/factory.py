"""Checkpointer 工厂：按运行环境在 MemorySaver / AsyncPostgresSaver 间切换。

设计要点（见 docs/plans/2026-08-01-postgres-checkpointer.md）：
- AsyncPostgresSaver 内部持连接池，**全进程共享一个单例**，绝不每请求新建。
- 开发环境（APP_ENV=development）用 MemorySaver，便于本地/notebook 单步调试；
  其余环境（staging/production，含 APP_ENV 未设置的 fail-closed 默认）用
  AsyncPostgresSaver，checkpoint 落 PG、跨重启持久、支持多实例。
- APP_ENV 判定复用 auth 的 fail-closed `app_env()`，消除本模块旧有的
  "dev" 与全局 "development" 语义不一致。
"""
from __future__ import annotations

import logging
import os

from langgraph.checkpoint.memory import MemorySaver

from app.infra.auth.startup_guard import app_env

logger = logging.getLogger(__name__)

_checkpointer = None
_pool = None


async def init_checkpointer() -> None:
    """启动期调用：按环境建立 checkpointer 单例；非开发环境顺带建 checkpoint 表。"""
    global _checkpointer, _pool

    if app_env() == "development":
        _checkpointer = MemorySaver()
        logger.info("checkpointer: MemorySaver (development)")
        return

    # 延迟导入：开发环境无需安装 psycopg3 也能跑。
    from psycopg_pool import AsyncConnectionPool
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conninfo = os.getenv("DATABASE_URL", "")
    if not conninfo:
        # fail-safe：非开发环境却没配 DATABASE_URL，降级 MemorySaver 并告警，
        # 而不是让进程起不来（checkpoint 非核心链路）。
        logger.warning("checkpointer: DATABASE_URL unset, falling back to MemorySaver")
        _checkpointer = MemorySaver()
        return

    # langgraph 要求连接 autocommit（它自管事务）。open=False + 显式 await open()
    # 是 psycopg-pool 在 async 上下文里的推荐用法。
    _pool = AsyncConnectionPool(
        conninfo=conninfo,
        open=False,
        kwargs={"autocommit": True},
    )
    await _pool.open()
    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()  # 自建 langgraph checkpoint 表
    logger.info("checkpointer: AsyncPostgresSaver (PostgreSQL)")


def get_checkpointer():
    """返回进程级 checkpointer 单例。未走 lifespan（测试/脚本）时兜底 MemorySaver。"""
    if _checkpointer is None:
        return MemorySaver()
    return _checkpointer


async def close_checkpointer() -> None:
    """关闭 checkpoint 连接池（开发环境用 MemorySaver 时无池可关）。"""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:
            logger.warning("close_checkpointer failed: %s", exc)
        _pool = None
