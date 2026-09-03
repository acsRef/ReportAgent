from __future__ import annotations

import json
import os
from typing import Any

import sqlglot
from sqlglot import exp as sql_exp
from sqlglot.tokens import TokenType
from decimal import Decimal

import psycopg2
import psycopg2.errors
import psycopg2.extras

PG_DSN = os.getenv("DATABASE_URL", "postgresql://ragent:ragent@localhost:5432/ragent")


def _analysis_dsn() -> str:
    """A-7 后半段（ragent 降权）：分析路径专用 DSN。

    LLM 生成的 SQL 必须走非超级用户 `ragent_readonly`（深度防御最后一环）：
    check_sql_safety 即使漏判，DB 层权限也拒绝服务端函数/系统表/写操作。
    Review-2 修正（fail-closed）：**ANALYSIS_DSN 是安全关键配置**——生产/未设
    APP_ENV 时缺失直接 raise（不允许退回普通 DATABASE_URL 取消第二层防线）；
    仅 `APP_ENV=development` 允许省略回退 PG_DSN（本地便捷，与 auth 启动闸
    dev-escape 同哲学）。
    """
    dsn = os.getenv("ANALYSIS_DSN")
    if dsn:
        return dsn
    from app.infra.auth.startup_guard import is_development

    if is_development():
        return PG_DSN
    raise RuntimeError(
        "ANALYSIS_DSN is required outside development: SQL 执行必须走只读角色 "
        "ragent_readonly（backend/scripts/setup_app_role.sql）——安全关键配置缺失 "
        "时系统 fail-closed，不退回普通 DATABASE_URL。"
    )


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
# Review-2 增补：咨询锁（pg_advisory_*）、序列推进（nextval/setval）、异步通知
# （pg_notify）——SELECT-only ≠ side-effect-free 的其余缺口；denylist 不是完整
# 安全边界，最后一环永远是 ragent_readonly DB role（见 _analysis_dsn fail-closed）。
# 精确名在小写集合里匹配；变体族（pg_sleep / pg_sleep_for / pg_sleep_until，
# dblink / dblink_exec / dblink_connect…）走前缀匹配。
_DANGEROUS_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_write_file", "pg_write_binary_file",
    "pg_ls_dir", "pg_ls_logdir", "pg_ls_waldir", "pg_stat_file",
    "lo_import", "lo_export", "lo_unlink", "lo_put",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "set_config",
    # Review-2：咨询锁 / 序列 / 通知（SELECT 调用也有锁/状态副作用）
    "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_try_advisory_lock", "pg_try_advisory_xact_lock",
    "nextval", "setval", "pg_notify",
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


def _check_select_side_effects(parsed: sql_exp.Expression, sql: str) -> tuple[bool, str]:
    """SELECT 顶层的隐性写/锁子句——AST 与词法黑名单的盲区（Final Hardening ②）。

    - `SELECT ... INTO <表>`：把查询结果写入一张真实表。INTO 不在关键字黑名单里
      （词法层 SET 拆词看不见），但落在 AST Select 的 `args["into"]`——显式拒绝。
    - `FOR UPDATE / FOR NO KEY UPDATE / FOR KEY SHARE / FOR SHARE`：行锁子句。
      sqlglot（默认与 postgres dialect）解析后把 lock 信息静默丢弃，AST 层不可见，
      只能回到 token 层逐 token 匹配关键词序列。Token 级扫描跳过字符串字面量与
      注释，`LIKE '%FOR UPDATE%'` 这类合法文本不会误伤（旧 regex 全文扫会命中）。
    """
    if isinstance(parsed, sql_exp.Select) and parsed.args.get("into") is not None:
        target = parsed.args["into"]
        # postgres dialect 下 into 是 exp.Into(this=Table(...))，剥一层拿目标表
        if isinstance(target, sql_exp.Into):
            target = target.this
        name = ""
        if isinstance(target, sql_exp.Table):
            name = target.name or ""
        elif isinstance(target, (list, tuple)) and target:
            first = target[0]
            name = first.name if isinstance(first, sql_exp.Table) else str(first)
        return False, f"禁止 SELECT INTO（写入目标表 {name or '?'}），仅支持只读查询"
    if not sql or not sql.strip():
        return True, ""
    try:
        dialect = sqlglot.Dialect.get_or_raise("postgres")
        tokens = [t for t in dialect.tokenize(sql) if t.token_type != TokenType.STRING]
    except Exception:
        tokens = []
    for i, tok in enumerate(tokens):
        if tok.text.upper() != "FOR":
            continue
        seq = [t.text.upper() for t in tokens[i + 1 : i + 4]]
        if (
            seq[:1] == ["UPDATE"]
            or seq[:3] == ["NO", "KEY", "UPDATE"]
            or seq[:1] == ["SHARE"]
            or seq[:2] == ["KEY", "SHARE"]
        ):
            return False, "禁止行锁子句（FOR UPDATE / FOR NO KEY UPDATE / FOR KEY SHARE / FOR SHARE），仅支持只读查询"
    return True, ""


def check_sql_safety(sql: str) -> tuple[bool, str]:
    """SQL 安全检查：SELECT-only + 关键字黑名单 + AST 五重校验。

    校验链：(1) 顶层必须是 SELECT（允许 WITH…SELECT 的 CTE 写法）；
    (2) DDL/DML 关键字黑名单；(3) sqlglot AST（postgres dialect）顶层节点
    必须是 Select；(4) SELECT 顶层隐性副作用——SELECT INTO / 行锁子句
    （Final Hardening ②，AST 与词法层盲区）；(5) 危险函数黑名单（A-1 闸 1）；
    (6) dim_/fact_ 表白名单（A-1 闸 2）。
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

    # AST parse（postgres dialect，与真实执行环境对齐）— reject anything
    # that isn't a SELECT statement
    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
        if not isinstance(parsed, sql_exp.Select):
            return False, "只允许 SELECT 查询语句"
    except Exception as e:
        return False, f"SQL 语法解析失败: {e}"

    # SELECT 顶层的隐性副作用（INTO / 行锁）——先于危险函数/白名单独立成闸
    ok, msg = _check_select_side_effects(parsed, sql)
    if not ok:
        return False, msg

    # A-1 两道 AST 闸：危险函数黑名单 + 表白名单（复用同一个 parsed 对象）
    ok, msg = _check_dangerous_functions(parsed)
    if not ok:
        return False, msg
    ok, msg = _check_table_whitelist(parsed)
    if not ok:
        return False, msg

    return True, ""


def _explain(sql: str) -> tuple[bool, str, str]:
    """PG EXPLAIN 门（Final Hardening ⑦）：(ok, error_msg, error_kind)。

    独立连接、用完即关；EXPLAIN 不执行查询，只验证 PG 接受该语句。
    """
    conn = _get_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}")
        return True, "", ""
    except Exception as exc:
        return False, str(exc)[:300], _classify_psycopg2_error(exc)
    finally:
        conn.close()


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

    ok, err, kind = _explain(sql)
    if not ok:
        return json.dumps(
            {"valid": False, "error": err, "error_kind": kind},
            ensure_ascii=False,
        )
    return json.dumps({"valid": True, "error": ""}, ensure_ascii=False)


def _execute_validated(sql: str) -> str:
    """执行**已通过 EXPLAIN 门**的只读 SELECT（Final Hardening ⑦ 内部路径）。

    调用方必须保证该 SQL 已经过 EXPLAIN 验证（sql_graph 的 `_validate` 节点
    之后、或公共 `execute_sql` 内部已自证）——本函数不再重复开连接跑 EXPLAIN。
    """
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
                # Final Hardening ③：numeric 列保持 Decimal 不转 float——下方
                # json.dumps(default=str) 会输出精确十进制字符串（"123456789012345678.91"），
                # 绝不在第一跳丢精度。int 列仍是 JSON number。double 列原样 float。
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


def execute_sql(sql: str) -> str:
    """执行只读 SELECT 查询，返回列结构和行数据。
    Final Hardening ⑦：execute_sql 自身强制 EXPLAIN 门——进程内任意直接调用
    （含注册给 LLM 的工具路径）都无法绕过 validate 直达执行；EXPLAIN 失败返回
    与执行失败同形状的错误 envelope（error_kind 分类），不落库不执行。
    输入：sql（合法 SELECT，字段名必须引用已确认的表结构）
    输出：{"columns": [{name, type}], "rows": [{col: value}], "error": string}
      - error 为空字符串表示成功
      - error 有内容表示执行失败（如字段不存在、语法错误）
    注意：连接的是只读 PostgreSQL 副本，无法修改数据。"""
    safe, msg = check_sql_safety(sql)
    if not safe:
        return json.dumps({"error": msg, "columns": [], "rows": []}, ensure_ascii=False)

    ok, err, kind = _explain(sql)
    if not ok:
        return json.dumps(
            {
                "error": err,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "error_kind": kind,
            },
            ensure_ascii=False,
        )
    return _execute_validated(sql)


def chart_advisor(sql_result: str) -> str:
    """根据已执行的查询结果推荐图表类型和配置。
    用途：SQL 执行成功并返回数据后，决定如何可视化。
    输入：sql_result（execute_sql 返回的完整 JSON，含 columns 和 rows）
    输出：{"type": "pie"|"bar"|"table", "config": {data, dimensions}}
    判断逻辑：
      - 1 个分类字段 + 1 个数值字段，行数 ≤ 8 → 饼图
      - 1 个分类字段 + 1 个数值字段，行数 > 8 → 柱状图
      - 无合适维度组合 → 纯表格
    数值列识别兼容 numeric 字符串（Decimal 全链字符串化后 rows 值为 str——
    裸 isinstance((int, float)) 会把金额列漏判成非数值）。
    不要用来：不执行 SQL 查询、不修改数据、不生成数值摘要（用 insight_analyst）。"""
    from app.utils.numbers import is_numeric_value

    data = json.loads(sql_result)
    columns_raw = data.get("columns", [])
    rows = data.get("rows", [])
    if not columns_raw or not rows:
        return json.dumps({"type": "table", "config": {}}, ensure_ascii=False)

    # columns can be dicts or plain strings
    columns = [c["name"] if isinstance(c, dict) else c for c in columns_raw]

    numeric_cols = [c for c in columns if is_numeric_value(rows[0].get(c))]
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
    处理方式：对前 3 个数值列分别计算（Decimal 精确算术，numeric 字符串同样可算）。
    空数据返回提示文本。
    不要用来：不执行 SQL 查询、不生成图表配置（用 chart_advisor）、不做趋势预测。"""
    from app.utils.numbers import to_decimal

    data = json.loads(sql_result)
    rows = data.get("rows", [])
    columns_raw = data.get("columns", [])
    if not rows:
        return "查询结果为空，无法生成洞察。"

    columns = [c["name"] if isinstance(c, dict) else c for c in columns_raw]
    numeric_cols = [c for c in columns if to_decimal(rows[0].get(c)) is not None]
    if not numeric_cols:
        return f"共 {len(rows)} 条记录，包含维度: {', '.join(columns)}。"

    insights = []
    for col in numeric_cols[:3]:
        values = [to_decimal(r[col]) for r in rows if r.get(col) is not None]
        values = [v for v in values if v is not None]
        if not values:
            continue
        total = sum(values, Decimal(0))
        avg = total / len(values)
        insights.append(f"{col}: 合计={total:,.2f}, 平均={avg:,.2f}, 最大={max(values):,.2f}, 最小={min(values):,.2f}")

    return "\n".join(insights)
