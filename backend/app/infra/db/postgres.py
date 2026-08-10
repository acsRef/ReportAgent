from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://ragent:ragent@localhost:5432/ragent",
)

# 池上限：分析期 SQL（psycopg2 独立路径）、应用持久化、observability、checkpoint
# 共享此池。20 支持 ~15 并发 SQL + 5 缓冲，避免 10 并发时连接排队拖慢 P95。
_POOL_MAX_SIZE = int(os.getenv("PG_POOL_MAX_SIZE", "20"))
_POOL_MONITOR_INTERVAL = float(os.getenv("PG_POOL_MONITOR_INTERVAL", "60.0"))

_pool: Optional[asyncpg.Pool] = None
_pool_monitor_task: Optional[asyncio.Task] = None


async def init_pool(dsn: str = POSTGRES_DSN) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=_POOL_MAX_SIZE)
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialized — call init_pool() first")
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def start_pool_monitor(interval: float = _POOL_MONITOR_INTERVAL) -> None:
    """启动后台监控任务，每 interval 秒打一次池状态。幂等：已有活动任务则跳过。"""
    global _pool_monitor_task
    if _pool_monitor_task is not None and not _pool_monitor_task.done():
        return
    _pool_monitor_task = asyncio.create_task(_pool_monitor_loop(interval))


def stop_pool_monitor() -> None:
    global _pool_monitor_task
    if _pool_monitor_task is not None:
        _pool_monitor_task.cancel()
        _pool_monitor_task = None


async def _pool_monitor_loop(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        pool = _pool
        if pool is None or pool.is_closing():
            continue
        _log_pool_status(pool)


def _log_pool_status(pool: asyncpg.Pool) -> None:
    """输出池状态。池耗尽（全占用、无空闲）时告警，否则 info。

    抽成独立函数便于离线单测——不依赖真实连接。
    """
    size = pool.get_size()
    idle = pool.get_idle_size()
    max_size = pool.get_max_size()
    if idle == 0 and size >= max_size:
        logger.warning("pg pool exhausted: size=%s idle=%s max=%s", size, idle, max_size)
    else:
        logger.info("pg pool status: size=%s idle=%s max=%s", size, idle, max_size)