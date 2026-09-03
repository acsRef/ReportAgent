"""PG 角色最小权限化的真 PG 断言（docs/plans/2026-08-05-pg-role-least-privilege.md）。

ANALYSIS_DSN 解析与「走非超级用户连接」由 unit 测覆盖（test_sql_limits.py）。
本文件验证 *真的* 拿到 PG 上时 ragent_readonly 是否被 PG 层成功挡住：

- 正常 BI（fact_orders）→ 成功
- 服务端文件读写（pg_read_file）→ permission denied for function
- 系统表读取（pg_authid）→ permission denied for table

测试条件：ANALYSIS_DSN 必须指向 ragent_readonly；否则 skip。
建角色脚本：backend/scripts/setup_app_role.sql。
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest

pytestmark = pytest.mark.persistence


ANALYSIS_DSN_ENV = "ANALYSIS_DSN"


def _analysis_dsn() -> str | None:
    return os.getenv(ANALYSIS_DSN_ENV) or None


pytestmark_skip = pytest.mark.skip(
    reason="ANALYSIS_DSN not set or does not target ragent_readonly",
)


@pytest.fixture
def dsn() -> str:
    dsn = _analysis_dsn()
    if not dsn or "ragent_readonly" not in dsn:
        pytestmark_skip()
    return dsn


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _connect(dsn: str):
    import psycopg2
    return psycopg2.connect(dsn, connect_timeout=5)


def test_normal_bi_query_succeeds(dsn: str) -> None:
    """ragent_readonly 对业务表 SELECT 仍可用。"""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fact_orders")
            (n,) = cur.fetchone()
    assert n > 0


def test_pg_read_file_is_blocked_at_db_layer(dsn: str) -> None:
    """check_sql_safety 之外，PG 层把 pg_read_file 直接挡住——深度防御最后一道。"""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            with pytest.raises(Exception) as exc_info:
                cur.execute("SELECT pg_read_file('/etc/passwd')")
    msg = str(exc_info.value).lower()
    assert "permission denied" in msg, f"ragent_readonly 不应能调 pg_read_file: {exc_info.value}"


def test_pg_authid_read_is_blocked_at_db_layer(dsn: str) -> None:
    """pg_authid 凭据表 —— ragent_readonly 不能读。"""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            with pytest.raises(Exception) as exc_info:
                cur.execute("SELECT * FROM pg_authid LIMIT 1")
    msg = str(exc_info.value).lower()
    assert "permission denied" in msg, f"ragent_readonly 不应能读 pg_authid: {exc_info.value}"


def test_app_schema_is_inaccessible(dsn: str) -> None:
    """app/agent/memory/observability schema 默认无 USAGE——分析路径看不见应用持久化数据。"""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            with pytest.raises(Exception) as exc_info:
                cur.execute("SELECT * FROM app.users LIMIT 1")
    msg = str(exc_info.value).lower()
    # PG 12+ 的 USAGE 缺失错误形如 "permission denied for schema app"
    assert "permission denied" in msg, f"应用 schema 不应对 ragent_readonly 可见: {exc_info.value}"