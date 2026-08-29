"""F4 修复钉子：memory.semantic_entry lifecycle migration 端到端测试（真 PG）。

P4b §Verification 显式要求 DB 端 e2e 钉子；钉住：
  1. lifecycle 列存在 + 默认值（scope='user' / confidence='medium' / status='active' /
     session_id=NULL / expires_at=NULL / updated_at NOW()）
  2. INSERT 旧 shape 行（无 lifecycle 入参）→ 默认值落对
  3. migration SQL 重跑 → 不报错（IF NOT EXISTS 守住）
  4. idx_semantic_entry_status_user 索引存在

Fixture 模式：psycopg2 直连 + monkeypatch-free（与 test_user_memory_eviction 一致）。
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.persistence


def _conn():
    import psycopg2
    return psycopg2.connect("postgresql://ragent:ragent@localhost:5432/ragent")


def _make_user_id() -> int:
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO app.users (username, password_hash) VALUES (%s, 'x') RETURNING id",
        (f"sem-mig-{uuid.uuid4().hex[:12]}",),
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


# init_pg.sql §157-181 的 P4b lifecycle migration 段——本测试复读以验证 idempotency
_LIFECYCLE_MIGRATION_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='scope') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN scope VARCHAR(16) DEFAULT 'user';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='confidence') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN confidence VARCHAR(16) DEFAULT 'medium';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='status') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN status VARCHAR(16) DEFAULT 'active';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='session_id') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN session_id VARCHAR(64);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='expires_at') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN expires_at TIMESTAMP;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='updated_at') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_semantic_entry_status_user
    ON memory.semantic_entry (user_id, status);
"""


def test_lifecycle_columns_exist_with_correct_defaults():
    """lifecycle 6 列存在 + 默认值（scope='user'/confidence='medium'/status='active'）。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name, column_default
        FROM information_schema.columns
        WHERE table_schema='memory' AND table_name='semantic_entry'
          AND column_name IN ('scope','confidence','status','session_id','expires_at','updated_at')
        ORDER BY column_name
    """)
    rows = cur.fetchall()
    conn.close()
    cols = {r[0]: r[1] for r in rows}
    assert set(cols.keys()) == {"scope", "confidence", "status", "session_id", "expires_at", "updated_at"}
    # scope='user' / confidence='medium' / status='active' 字面钉子（迁移段默认值）
    assert "'user'" in (cols["scope"] or "")
    assert "'medium'" in (cols["confidence"] or "")
    assert "'active'" in (cols["status"] or "")
    # session_id / expires_at 无默认（NULL 隐式）
    assert cols["session_id"] is None
    assert cols["expires_at"] is None


def test_insert_old_shape_row_gets_lifecycle_defaults():
    """INSERT 旧 shape 行（仅 user_id/content/memory_type/importance_score，无 lifecycle 入参）
    → 默认值落 scope='user' / confidence='medium' / status='active' / session_id=NULL / expires_at=NULL。"""
    user_id = _make_user_id()
    try:
        conn = _conn()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO memory.semantic_entry
               (user_id, content, memory_type, importance_score)
               VALUES (%s, %s, 'insight', 0.5)
               RETURNING scope, confidence, status, session_id, expires_at""",
            (user_id, "old-shape-row"),
        )
        row = cur.fetchone()
        conn.close()
        assert row[0] == "user"
        assert row[1] == "medium"
        assert row[2] == "active"
        assert row[3] is None
        assert row[4] is None
    finally:
        _cleanup(user_id)


def test_migration_is_idempotent():
    """重跑 P4b lifecycle migration 段 → 不报错（IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 守住）。"""
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()
    # 第一次跑（fresh DB 应 no-op；旧 DB 应填补缺失列）
    cur.execute(_LIFECYCLE_MIGRATION_SQL)
    # 第二次跑同段 → 仍不报错（idempotent 是 P4b migration 的硬约束）
    cur.execute(_LIFECYCLE_MIGRATION_SQL)
    conn.close()


def test_status_user_index_exists():
    """idx_semantic_entry_status_user 索引存在——P4b active-only 召回过滤依赖。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM pg_indexes
        WHERE schemaname='memory' AND tablename='semantic_entry'
          AND indexname='idx_semantic_entry_status_user'
    """)
    row = cur.fetchone()
    conn.close()
    assert row is not None, "idx_semantic_entry_status_user 索引不存在"