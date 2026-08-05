"""A-1 安全闸测试：check_sql_safety 的危险函数黑名单 + dim_/fact_ 表白名单。

背景（docs/plans/2026-08-04-agent-security-hardening.md）：旧三层校验只拦
DDL/DML 关键字与顶层非 SELECT，`SELECT pg_read_file('/etc/passwd')`、
`SELECT * FROM pg_authid`、`dblink(...)` 全部放行。ragent 在 docker 里是
PG 超级用户，这些 SELECT 可达服务端文件读写与凭据泄露。

本文件全部离线可跑（不需要 PG）：
- check_sql_safety 是纯 AST 校验；
- validate_sql 对危险 SQL 在连 PG 之前就短路返回 {"valid": false}。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.smoke


# --- 危险函数黑名单（闸 1）------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        # 服务端文件读写 / 目录列举
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('/etc/passwd')",
        "SELECT pg_write_file('/tmp/pwn')",
        "SELECT pg_ls_dir('/')",
        "SELECT pg_stat_file('/etc/passwd')",
        # 大对象导入导出
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(12345, '/tmp/exfil')",
        "SELECT lo_unlink(12345)",
        # dblink 外联（前缀匹配覆盖变体）
        "SELECT dblink('host=evil dbname=x', 'SELECT 1')",
        "SELECT dblink_connect('host=evil')",
        # 时间侧信道 / DoS（前缀匹配覆盖 pg_sleep_for / pg_sleep_until）
        "SELECT pg_sleep(10)",
        "SELECT pg_sleep_for('10 seconds')",
        # 进程操控 / 配置篡改
        "SELECT pg_terminate_backend(42)",
        "SELECT pg_cancel_backend(42)",
        "SELECT pg_reload_conf()",
        "SELECT set_config('log_statement', 'all', false)",
        # 危险函数藏在 JOIN 后的子查询里也要命中
        "SELECT * FROM fact_sales WHERE amount > (SELECT pg_read_file('/etc/passwd'))",
    ],
)
def test_dangerous_function_rejected(sql):
    from app.tools.sql_tools import check_sql_safety

    safe, msg = check_sql_safety(sql)
    assert safe is False
    assert msg  # 拒绝原因必须非空——它会喂回重试 prompt


def test_dangerous_function_message_names_the_function():
    """错误信息带上函数名，重试 prompt 才能让 LLM 定向修正。"""
    from app.tools.sql_tools import check_sql_safety

    safe, msg = check_sql_safety("SELECT pg_read_file('/etc/passwd')")
    assert safe is False
    assert "pg_read_file" in msg


# --- 表白名单（闸 2）------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        # 裸系统表（无 schema 前缀）
        "SELECT * FROM pg_authid",
        "SELECT usename, passwd FROM pg_shadow",
        # pg_catalog / information_schema
        "SELECT * FROM pg_catalog.pg_class",
        "SELECT * FROM information_schema.tables",
        # 应用 schema（凭据 / 会话 / 记忆 / 追踪全在里面）
        "SELECT * FROM app.users",
        "SELECT * FROM agent.sessions",
        "SELECT * FROM memory.semantic_entry",
        "SELECT * FROM observability.agent_trace",
        # 无前缀的普通表也不放行
        "SELECT * FROM users",
        # 合法表 JOIN 非法表同样拒绝
        "SELECT * FROM fact_sales JOIN pg_authid ON true",
        # 跨 catalog 一律拒绝（即使表名合法）
        "SELECT * FROM postgres.public.fact_sales",
        # WITH 前缀放行给 CTE SELECT，但 WITH 包裹的 DML 必须被拦
        "WITH x AS (SELECT 1) INSERT INTO users VALUES (1)",
    ],
)
def test_non_whitelisted_table_rejected(sql):
    from app.tools.sql_tools import check_sql_safety

    safe, msg = check_sql_safety(sql)
    assert safe is False
    assert msg


def test_table_whitelist_message_names_the_table():
    from app.tools.sql_tools import check_sql_safety

    safe, msg = check_sql_safety("SELECT * FROM pg_authid")
    assert safe is False
    assert "pg_authid" in msg


# --- 正常 BI 查询不受影响 -------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM fact_sales",
        "SELECT region_id, SUM(amount) AS total FROM fact_sales GROUP BY region_id",
        "SELECT s.sale_id, d.full_date FROM fact_sales s JOIN dim_date d ON s.date_id = d.date_id",
        # CTE 别名不是真实表，不应被白名单拦
        "WITH src AS (SELECT * FROM fact_sales WHERE amount > 100) "
        "SELECT region_id, SUM(amount) FROM src GROUP BY region_id",
        # 显式 public schema 合法
        "SELECT * FROM public.dim_product",
        # PG 折叠未加引号的大写标识符，闸内按小写比较
        "SELECT * FROM FACT_SALES",
        # 无表查询（SELECT 1 / 纯聚合）不触碰任何表，放行
        "SELECT 1",
        "SELECT COUNT(*) AS n FROM fact_returns r JOIN fact_sales s ON r.sale_id = s.sale_id",
    ],
)
def test_normal_bi_query_allowed(sql):
    from app.tools.sql_tools import check_sql_safety

    safe, msg = check_sql_safety(sql)
    assert safe is True, f"正常查询被误拦: {msg}"
    assert msg == ""


# --- validate_sql 集成：连 PG 之前短路 ------------------------------------------

def test_validate_sql_short_circuits_before_pg_for_dangerous_function():
    """危险函数在 AST 闸就被拒，validate_sql 不应也无需触达 PG。"""
    from app.tools.sql_tools import validate_sql

    result = json.loads(validate_sql("SELECT pg_read_file('/etc/passwd')"))
    assert result["valid"] is False
    assert "pg_read_file" in result["error"]


def test_validate_sql_short_circuits_before_pg_for_non_whitelisted_table():
    from app.tools.sql_tools import validate_sql

    result = json.loads(validate_sql("SELECT * FROM pg_authid"))
    assert result["valid"] is False
    assert "pg_authid" in result["error"]
