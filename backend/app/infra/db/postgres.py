from __future__ import annotations

import os
from typing import Optional

import asyncpg

POSTGRES_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://ragent:ragent@localhost:5432/ragent",
)

_pool: Optional[asyncpg.Pool] = None


async def init_pool(dsn: str = POSTGRES_DSN) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
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
