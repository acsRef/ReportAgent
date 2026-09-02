from __future__ import annotations

import json
import os
from typing import Any

import sqlglot
from sqlglot import exp as sql_exp
import psycopg2
import psycopg2.errors
import psycopg2.extras

from decimal import Decimal

PG_DSN = os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")


def _analysis_dsn() -> str:
    """A-7 后半段（ragent 降权）：分析路径专用 DSN。

    走非超级用户执行 LLM 生成的 SELECT；未配置时回退到 `PG_DSN`（向后兼容）。
    配置见 `backend/scripts/setup_app_role.sql` 与 `ANALYSIS_DSN` 环境变量。
    """
    return os.getenv("ANALYSIS_DSN") or PG_DSN


# 硬上限：单条 SQL 最多返回 5000 行；超出时通过 total_rows + truncated 字段告知 LLM。
# 1M 行的查询不再把全部行塞进 JSON / SSE / JSONB。
MAX_RESULT_ROWS = 5000

# 连接 / 查询硬超时。PG 端 statement_timeout 按 ms 计（这里 30s）。
# 客户端 connect_timeout 10s 防 PG 不可达时无限制挂着。
CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 30_000

ErrorKind = str  # "timeout" | "syntax" | "object" | "object_not_found" | "object_ambiguous" | "connection" | "permission" | "other"


def _classify_psycopg2_error(exc: BaseException) -> ErrorKind:
    """把 psycopg2 异常分类为 8 个枚举之一，供上层决定重试策略。

    P15 prelude fix：用 psycopg2.errors 具体子类替代 ProgrammingError 兜底——
    UndefinedColumn/Table/Function 归 'object_not_found'（DiagnosePolicy 走 MCP
    schema retrieval 路径），AmbiguousColumn 归 'object_ambiguous'（直接 clarify），
    DivisionByZero/DatatypeMismatch 归 'other'（LLM 修不了）。
    边界保留：timeout / connection / permission / syntax 不进 LLM retry。

    精确子类优先级高于 message 字符串匹配（psycopg2 子类关系比 message 内容可靠）；
    旧独立 isinstance 分支（UndefinedColumn/Table/Function/SyntaxError）合并进
    ProgrammingError 分支内的精确子类检查。
    """
    # timeout / connection 边界保留
    if isinstance(exc, (psycopg2.errors.QueryCanceled, psycopg2.errors.AdminShutdown, psycopg2.errors.CrashShutdown)):
        return "timeout"
    if isinstance(exc, psycopg2.OperationalError):
        return "connection"
    # ProgrammingError 细分
    if isinstance(exc, psycopg2.ProgrammingError):
        msg = str(exc).lower()
        if "permission" in msg or "denied" in msg:
            return "permission"
        if "syntax" in msg or "parse" in msg:
            return "syntax"
        # 精确子类优先级高于兜底（独立于 message 匹配）
        if isinstance(exc, psycopg2.errors.SyntaxError):
            return "syntax"
        if isinstance(exc, (psycopg2.errors.UndefinedColumn,
                            psycopg2.errors.UndefinedTable,
                            psycopg2.errors.UndefinedFunction)):
            return "object_not_found"
        if isinstance(exc, psycopg2.errors.AmbiguousColumn):
            return "object_ambiguous"
        if isinstance(exc, (psycopg2.errors.DivisionByZero,
                            psycopg2.errors.DatatypeMismatch)):
            return "other"
        # 未识别 ProgrammingError 兜底保留 'object'（向后兼容）
        return "object"
    return "other"


def _get_pg_conn():
    """创建同步 PG 连接——带 connect_timeout 与 statement_timeout。

    A-7 后半段：分析路径走 `ANALYSIS_DSN`（非超级用户 ragent_readonly），
    深度防御最后一环——即便 check_sql_safety 五重闸漏判，DB 层权限也会拒绝
    服务端文件读写等操作。
    """
    return psycopg2.connect(
        _analysis_dsn(),
        connect_timeout=CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
    )


DANGEROUS_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "TRUNCATE", "CREATE", "REPLACE", "GRANT", "REVOKE",
    "EXECUTE", "EXEC", "CALL", "MERGE", "LOAD",
]

# A-1：危险函数黑名单——服务端文件读写 / 目录列举 / 大对象导入导出 / 进程操控 /
# 配置篡改。这些函数即使是 SELECT 调用也有真实副作用（读服务器文件、杀其它会话）。
# 精确名在小写集合里匹配；变体族（pg_sleep / pg_sleep_for / pg_sleep_until，
# dblink / dblink_exec / dblink_connect…）走前缀匹配。
_DANGEROUS_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_write_file", "pg_write_binary_file",
    "pg_ls_dir", "pg_ls_logdir", "pg_ls_waldir", "pg_stat_file",
    "lo_import", "lo_export", "lo_unlink", "lo_put",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "set_config",
}
_DANGEROUS_FUNCTION_PREFIXES = ("pg_sleep", "dblink")

# A-1：表白名单——分析库只有 public 下的 dim_* / fact_* 星型模型表。
# pg_catalog / information_schema / app / agent / memory / observability
# 以及裸系统表（pg_authid 等）全部落在闸外。
_ALLOWED_TABLE_PREFIXES = ("dim_", "fact_")


def _check_dangerous_functions(parsed: sql_exp.Expression) -> tuple[bool, str]:
    """A-1 闸 1：遍历 AST 全部函数节点，命中危险函数黑名单即拒。

    sqlglot 把 PG 专有函数（pg_read_file / dblink 等）解析为 ``sql_exp.Anonymous``，
    函数名在 ``.this``；已知内建函数子类取类名兜底，防止个别函数被 sqlglot
    建模为具名类时漏检。
    """
    for node in parsed.find_all(sql_exp.Func):
        if isinstance(node, sql_exp.Anonymous):
            name = str(node.this).lower()
        else:
            name = type(node).__name__.lower()
        if name in _DANGEROUS_FUNCTIONS or name.startswith(_DANGEROUS_FUNCTION_PREFIXES):
            return False, f"禁止调用危险函数 {name}，仅支持对业务表的只读查询"
    return True, ""


def _check_table_whitelist(parsed: sql_exp.Expression) -> tuple[bool, str]:
    """A-1 闸 2：只允许 public（或省略 schema）下的 dim_* / fact_* 表。

    CTE 别名不是真实表，跳过；带 catalog（``db.schema.table``）一律拒绝。
    """
    cte_aliases = {cte.alias_or_name.lower() for cte in parsed.find_all(sql_exp.CTE)}
    for table in parsed.find_all(sql_exp.Table):
        name = (table.name or "").lower()
        if not name or name in cte_aliases:
            continue
        if table.catalog:
            return False, f"禁止跨 catalog 访问: {name}"
        schema = (table.db or "").lower()
        if schema and schema != "public":
            return False, f"只允许查询 public schema 下的业务表，拒绝 {schema}.{name}"
        if not name.startswith(_ALLOWED_TABLE_PREFIXES):
            return False, f"只允许查询 dim_/fact_ 前缀的业务表，拒绝 {name}"
    return True, ""


def check_sql_safety(sql: str) -> tuple[bool, str]:
    """SQL 安全检查：SELECT-only + 关键字黑名单 + AST 五重校验。

    校验链：(1) 顶层必须是 SELECT（允许 WITH…SELECT 的 CTE 写法）；
    (2) DDL/DML 关键字黑名单；(3) sqlglot AST 顶层节点必须是 Select；
    (4) 危险函数黑名单（A-1 闸 1）；(5) dim_/fact_ 表白名单（A-1 闸 2）。
    任一失败返回 (False, 原因)。
    """
    sql_upper = sql.strip().upper()

    # WITH 前缀放行给 CTE 写法；`WITH x AS (…) INSERT/UPDATE…` 会先撞关键字
    # 黑名单，剩下的由 AST「顶层必须是 Select」兜底。
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return False, "只允许 SELECT 查询语句"

    tokens = set(sql_upper.replace("(", " ").replace(")", " ").replace(";", " ").split())
    for kw in DANGEROUS_KEYWORDS:
        if kw in tokens:
            return False, f"禁止使用 {kw}，仅支持只读查询"

    # AST parse — reject anything that isn't a SELECT statement
    try:
        parsed = sqlglot.parse_one(sql)
        if not isinstance(parsed, sql_exp.Select):
            return False, "只允许 SELECT 查询语句"
    except Exception as e:
        return False, f"SQL 语法解析失败: {e}"

    # A-1 两道 AST 闸：危险函数黑名单 + 表白名单（复用同一个 parsed 对象）
    ok, msg = _check_dangerous_functions(parsed)
    if not ok:
        return False, msg
    ok, msg = _check_table_whitelist(parsed)
    if not ok:
        return False, msg

    return True, ""


def validate_sql(sql: str) -> str:
    """三重校验 SQL 语法和安全性，不执行查询。
    用途：每次 execute_sql 前的安全检查。必须校验通过后才能执行。
    校验链：(1) 黑名单检查（禁止 INSERT/UPDATE/DELETE/DROP 等 DDL/DML）
           (2) AST 解析（sqlglot 验证是标准 SELECT；危险函数黑名单；
               只允许 public 下 dim_/fact_ 前缀的业务表）
           (3) EXPLAIN 执行（在实际 PG 连接上跑 EXPLAIN 捕获语法错误）
    输入：sql（要校验的 SQL 文本）
    输出：{"valid": bool, "error": string}
    约束：只接受 SELECT 语句。校验通过不代表逻辑正确，只代表安全可执行。
    不要用来：不要用它执行查询或获取数据。"""
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"valid": False, "error": msg}, ensure_ascii=False)

    conn = _get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
        return json.dumps({"valid": True, "error": ""}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(
            {
                "valid": False,
                "error": str(exc)[:300],
                "error_kind": _classify_psycopg2_error(exc),
            },
            ensure_ascii=False,
        )
    finally:
        conn.close()


def execute_sql(sql: str) -> str:
    """执行只读 SELECT 查询，返回列结构和行数据。
    前置条件：必须先调 validate_sql 且返回 {"valid": true}
    安全限制：只接受 SELECT 语句。任何 DDL/DML 都会被拒绝。
    输入：sql（合法 SELECT，字段名必须引用已确认的表结构）
    输出：{"columns": [{name, type}], "rows": [{col: value}], "error": string}
      - error 为空字符串表示成功
      - error 有内容表示执行失败（如字段不存在、语法错误）
    重试策略：失败最多重试 3 次，每次携带错误信息回 LLM 修正 SQL。
              3 次全失败则转 clarify 节点要求用户澄清。
    注意：连接的是只读 PostgreSQL 副本，无法修改数据。"""
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"error": msg, "columns": [], "rows": []}, ensure_ascii=False)

    conn = _get_pg_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 用 CTE 一次往返拿「全部行数 + 截断后的行集」：单次 query 内 count
            # 不会因并发而漂移，索引走主键的查询无显著成本。MAX_RESULT_ROWS+1
            # 让 count>MAX 时仍能区分是否被截断。
            cur.execute(
                "WITH src AS (" + sql.rstrip().rstrip(";") + f") "
                f"SELECT *, (SELECT count(*) FROM src) AS _total FROM src LIMIT {MAX_RESULT_ROWS + 1}"
            )
            columns = [{"name": desc.name, "type": str(desc.type_code)} for desc in cur.description if desc.name != "_total"]
            raw_rows = cur.fetchall()
            total = int(raw_rows[0]["_total"]) if raw_rows else 0
            rows: list[dict[str, Any]] = []
            for row in raw_rows:
                row_dict = {k: v for k, v in dict(row).items() if k != "_total"}
                for k, v in list(row_dict.items()):
                    if isinstance(v, Decimal):
                        row_dict[k] = float(v)
                rows.append(row_dict)
            truncated = total > MAX_RESULT_ROWS or len(rows) > MAX_RESULT_ROWS
            if truncated and len(rows) > MAX_RESULT_ROWS:
                rows = rows[:MAX_RESULT_ROWS]
            return json.dumps(
                {
                    "columns": columns,
                    "rows": rows,
                    "row_count": total,
                    "truncated": truncated,
                },
                ensure_ascii=False,
                default=str,
            )
    except Exception as exc:
        return json.dumps(
            {
                "error": str(exc)[:300],
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "error_kind": _classify_psycopg2_error(exc),
            },
            ensure_ascii=False,
        )
    finally:
        conn.close()


def chart_advisor(sql_result: str) -> str:
    """根据已执行的查询结果推荐图表类型和配置。
    用途：SQL 执行成功并返回数据后，决定如何可视化。
    输入：sql_result（execute_sql 返回的完整 JSON，含 columns 和 rows）
    输出：{"type": "pie"|"bar"|"table", "config": {data, dimensions}}
    判断逻辑：
      - 1 个分类字段 + 1 个数值字段，行数 ≤ 8 → 饼图
      - 1 个分类字段 + 1 个数值字段，行数 > 8 → 柱状图
      - 无合适维度组合 → 纯表格
    不要用来：不执行 SQL 查询、不修改数据、不生成数值摘要（用 insight_analyst）。"""
    data = json.loads(sql_result)
    columns_raw = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns_raw or not rows:
        return json.dumps({"type": "table", "config": {}}, ensure_ascii=False)

    # columns can be dicts or plain strings
    columns = [c["name"] if isinstance(c, dict) else c for c in columns_raw]

    numeric_cols = [c for c in columns if isinstance(rows[0].get(c), (int, float))]
    categorical_cols = [c for c in columns if c not in numeric_cols]

    if categorical_cols and numeric_cols:
        cat = categorical_cols[0]
        num = numeric_cols[0]
        if len(rows) <= 8:
            return json.dumps({
                "type": "pie",
                "config": {"data": rows, "dimensions": {"category": cat, "value": num}},
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "type": "bar",
                "config": {"data": rows, "dimensions": {"x": cat, "y": num}},
            }, ensure_ascii=False)

    return json.dumps({"type": "table", "config": {"data": rows}}, ensure_ascii=False)


def insight_analyst(sql_result: str) -> str:
    """对查询结果的数值列生成统计摘要（合计、均值、最大、最小）。
    用途：SQL 执行成功并返回数据后，提炼数值维度的核心指标。
    输入：sql_result（execute_sql 返回的完整 JSON，含 columns 和 rows）
    输出：多行文本，每行对应一个数值列的统计，如 "销售额: 合计=1,234,567.00, 平均=102,880.58, 最大=999,999.00, 最小=1.00"
    处理方式：对前 3 个数值列分别计算。空数据返回提示文本。
    不要用来：不执行 SQL 查询、不生成图表配置（用 chart_advisor）、不做趋势预测。"""
    data = json.loads(sql_result)
    rows = data.get("rows", [])
    columns_raw = data.get("columns", [])
    if not rows:
        return "查询结果为空，无法生成洞察。"

    columns = [c["name"] if isinstance(c, dict) else c for c in columns_raw]
    numeric_cols = [c for c in columns if rows and isinstance(rows[0].get(c), (int, float))]
    if not numeric_cols:
        return f"共 {len(rows)} 条记录，包含维度: {', '.join(columns)}。"

    insights = []
    for col in numeric_cols[:3]:
        values = [r[col] for r in rows if r.get(col) is not None]
        if not values:
            continue
        total = sum(values)
        avg = total / len(values)
        max_val = max(values)
        min_val = min(values)
        insights.append(f"{col}: 合计={total:,.2f}, 平均={avg:,.2f}, 最大={max_val:,.2f}, 最小={min_val:,.2f}")

    return "\n".join(insights)
