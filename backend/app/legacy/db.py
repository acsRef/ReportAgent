from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

# legacy: 本模块已从 app/ 迁入 app/legacy/（P1 归置）。路径深度 +1，
# DuckDB 文件仍落在 backend/ 根，与迁移前一致。
_DB_PATH = Path(__file__).parent.parent.parent / "report.duckdb"
_SEED_SQL_PATH = Path(__file__).parent.parent.parent / "seed_data.sql"

_conn_rw: duckdb.DuckDBPyConnection | None = None
_conn_ro: duckdb.DuckDBPyConnection | None = None

def get_connection() -> duckdb.DuckDBPyConnection:
    global _conn_rw
    if _conn_rw is None:
        _conn_rw = duckdb.connect(str(_DB_PATH), read_only=False)
        _seed_if_empty(_conn_rw)
    return _conn_rw

def get_readonly_connection() -> duckdb.DuckDBPyConnection:
    return get_connection()


def _seed_if_empty(conn: duckdb.DuckDBPyConnection) -> None:
    tables = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
    ).fetchone()[0]
    if tables > 0:
        return

    if not _SEED_SQL_PATH.exists():
        raise FileNotFoundError(f"Seed SQL not found: {_SEED_SQL_PATH}")

    sql = _SEED_SQL_PATH.read_text(encoding="utf-8")
    statements = sql.strip().split(";")
    for stmt in statements:
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception as exc:
                logger.warning("seed statement skipped (%s): %s", exc, stmt[:80])

    conn.commit()
    logger.info("Database seeded: %s", _DB_PATH)


def close_connection() -> None:
    global _conn_rw, _conn_ro
    if _conn_rw is not None:
        _conn_rw.close()
        _conn_rw = None
    if _conn_ro is not None:
        _conn_ro.close()
        _conn_ro = None