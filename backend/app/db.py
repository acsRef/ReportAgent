from __future__ import annotations

import os
from pathlib import Path

import duckdb

_DB_PATH = Path(__file__).parent.parent / "report.duckdb"
_SEED_SQL_PATH = Path(__file__).parent.parent / "seed_data.sql"

_conn: duckdb.DuckDBPyConnection | None = None


def get_connection() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        _conn = duckdb.connect(str(_DB_PATH), read_only=False)
        _seed_if_empty(_conn)
    return _conn


def get_readonly_connection() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(_DB_PATH), read_only=True)
    return conn


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
                print(f"[db] Warning: seed statement skipped ({exc}): {stmt[:80]}")

    conn.commit()
    print(f"[db] Database seeded: {_DB_PATH}")


def close_connection() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
