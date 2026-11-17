"""运行记录（SQLite）：每次评测都留可追溯证据。

JD 第 5 条要「可追溯记录 + 适用范围 / 风险 / 限制 / 证据状态 / 版本 / 运行环境」——
这些字段全部落在 `runs` 表里，页面「历史」页按条展开。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("EVAL_DB", str(Path(__file__).with_name("runs.sqlite"))))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    project     TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    tier        TEXT,
    latency_ms  REAL,
    env         TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def save_run(result: dict, *, env: dict) -> str:
    """落一条运行记录，返回 run_id。"""
    init_db()
    run_id = result.get("run_id") or f"run-{uuid.uuid4().hex[:10]}"
    result = {**result, "run_id": run_id}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, created_at, project, case_id, title, verdict, tier, "
            "latency_ms, env, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                result.get("project", ""),
                result.get("case_id", ""),
                result.get("title", ""),
                result.get("verdict", ""),
                result.get("tier", ""),
                float(result.get("latency_ms") or 0),
                json.dumps(env, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
            ),
        )
    return run_id


def list_runs(limit: int = 50) -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT run_id, created_at, project, case_id, title, verdict, tier, latency_ms "
            "FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "env": json.loads(row["env"]), "payload": json.loads(row["payload"])}


def stats() -> dict:
    """总览用：总数 + 按 verdict / project 分组。"""
    init_db()
    with _connect() as conn:
        total = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
        by_verdict = {
            r[0]: r[1]
            for r in conn.execute("SELECT verdict, count(*) FROM runs GROUP BY verdict")
        }
        by_project = {
            r[0]: r[1]
            for r in conn.execute("SELECT project, count(*) FROM runs GROUP BY project")
        }
    return {"total": total, "by_verdict": by_verdict, "by_project": by_project}
