> 状态: 已完成（setup_app_role.sql 幂等建 ragent_readonly + GRANT SELECT dim_/fact_；ANALYSIS_DSN 解析 + _get_pg_conn 切换；.env.example 注释；ANALYSIS_DSN 测试 4 项全过；ragent_readonly 真连：pg_read_file/pg_authid/app schema 全部 PG 层挡；全量 274 passed / 1 skipped e2e）

# PG 角色最小权限化：分析路径走独立非超级用户

## Context

2026-08-04 安全加固 plan 的「Explicitly NOT doing」遗留项：「不把 ragent 换成非超级用户角色
（DDL/权限重构，单独开 plan；本次靠校验层闭环）」——本 plan 即该项。

`check_sql_safety` 五重闸已堵住危险函数 + 非 dim_/fact_ 表，但 A-1 校验只覆盖已知模式：
- 未来 PG 版本引入新 server-side 函数 / extension：`pg_*`、`file_*` 等族名变化或新增
  可能跳出黑名单；
- 校验层本身有 BUG / 漏配：单点失守即绕过；
- `ragent` 在 docker 里是 PG 超级用户，`SELECT pg_read_file('/etc/passwd')` 即便过闸也会
  在执行层成功——属于深度防御的最后一环。

**根因不是规则不全，而是执行主体权限过大**。本次把分析路径的执行主体换成非超级用户，
堵掉整个攻击面（即便 SQL 写入也跑不通），同时不影响应用持久化与 checkpoint。

## Design

### 1. 最小爆破面：拆分 DSN

不动 `DATABASE_URL`（应用持久化 + langgraph checkpointer 继续走超级用户）；新增
`ANALYSIS_DSN` 给 `sql_tools.py`（psycopg2 同步路径）专用。两者解耦：

| 用途 | 角色 | 走哪个连接 |
|---|---|---|
| 应用持久化（asyncpg pool） | `ragent`（PG 超级用户，不变） | `DATABASE_URL` |
| LangGraph checkpoint（autocommit psycopg3） | `ragent`（PG 超级用户，不变） | `DATABASE_URL` |
| LLM 生成的 SQL 执行（psycopg2） | `ragent_readonly`（**非超级用户**） | `ANALYSIS_DSN` |

理由：app persistence 与 checkpoint 在受信任代码里跑，安全责任在代码层；分析路径跑的是
LLM 生成的、未审 SQL，安全责任必须落到 DB 层。

### 2. 新角色 `ragent_readonly`（setup_app_role.sql，幂等）

```
- LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
- PASSWORD ragent_readonly（开发用；生产经密钥管理）
- GRANT USAGE ON SCHEMA public
- GRANT SELECT ON public.dim_date / dim_region / dim_product / dim_customer /
  dim_warehouse / dim_employee / fact_sales / fact_returns / fact_inventory /
  fact_attendance
- 不授予 pg_catalog 默认权限之外的额外 GRANT
```

结果：即使 SQL 通过 A-1 闸，落到该角色上 `SELECT pg_read_file(...)` / `SELECT * FROM
pg_authid` / `SELECT lo_export(...)` / `SELECT set_config(...)` 等都会 PG 层 `ERROR:
permission denied for function/table pg_*`，无论校验是否漏判。

### 3. 代码改动（最小）

- `backend/app/tools/sql_tools.py`：新增常量 `ANALYSIS_DSN = os.getenv("ANALYSIS_DSN",
  PG_DSN)`；`_get_pg_conn()` 使用 `ANALYSIS_DSN`。原有 `PG_DSN`（= DATABASE_URL）保留
  作为 fallback，便于未配 `ANALYSIS_DSN` 的旧环境继续工作（开发阶段）。
- `backend/.env.example`：新增 `ANALYSIS_DSN` 注释与示例值。
- 用户级 `.env`：用户自行追加 `ANALYSIS_DSN=postgresql://ragent_readonly:ragent_readonly@localhost:5432/ragent`
  并 docker exec 跑一次 `setup_app_role.sql`。`backend/scripts/setup_app_role.sql`
  作为交付物提交。

### 4. 测试（offline + persistence）

- offline 单测：mock `_get_pg_conn` 后断言 `validate_sql` 与 `execute_sql` 走的 DSN
  含 `ragent_readonly`。
- persistence 标记实测（`pytest -m persistence`）：用真 `ANALYSIS_DSN` 连接
  `ragent_readonly`，断言：
  - `SELECT * FROM fact_sales LIMIT 1` → 成功；
  - `SELECT pg_read_file('/etc/passwd')` → `ProgrammingError("permission denied")`；
  - `SELECT * FROM pg_authid` → `permission denied`；
  - 当前 `MAX_RESULT_ROWS` 截断逻辑不受影响。
- 全量 267 passed / 1 skipped(e2e) 无回归。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/scripts/setup_app_role.sql`（新） | 幂等创建 `ragent_readonly` 角色 + GRANT SELECT（DO 块护 IF NOT EXISTS） |
| `backend/app/tools/sql_tools.py` | `ANALYSIS_DSN = os.getenv("ANALYSIS_DSN", PG_DSN)`；`_get_pg_conn` 改用 `ANALYSIS_DSN` |
| `backend/.env.example` | 新增 `ANALYSIS_DSN` 行 + 注释 |
| `backend/tests/test_sql_limits.py` | 新增 ANALYSIS_DSN 解析的单测 + ragent_readonly 连接的 persistence 断言 |
| `docs/plans/README.md` | 索引登记 |

## Reused existing utilities

- `_get_pg_conn` 是 sql_tools 的唯一 psycopg2 入口；切换 DSN 影响面即此函数。
- `MAX_RESULT_ROWS` / `execute_sql` 的 CTE 包装逻辑不变——只是换连接目标。
- `init_pool` / checkpointer / 其它 asyncpg 路径零改动。

## Verification

```bash
# 一次性：建角色
docker exec -i ragent-postgres psql -U ragent -d ragent \
  < backend/scripts/setup_app_role.sql

# 然后 .env 加 ANALYSIS_DSN 后：
cd backend
pytest tests/test_sql_limits.py -q -k ANALYSIS_DSN   # offline 单测
pytest -m persistence -q                             # ragent_readonly 真连
pytest -q                                            # 全量无回归
```

手工矩阵：

| 场景 | 预期 |
|---|---|
| `ragent_readonly` 连 PG 执行 `SELECT pg_read_file('/etc/passwd')` | PG 层 `permission denied`，不依赖 check_sql_safety |
| `ragent_readonly` 执行 `SELECT * FROM pg_authid` | permission denied |
| `ragent_readonly` 执行正常 BI（`SELECT * FROM fact_sales LIMIT 5`） | 成功 |
| 不配 `ANALYSIS_DSN`，仅 `DATABASE_URL` | fallback 到旧 DSN，旧路径仍工作（开发阶段） |

## Explicitly NOT doing

- **不**改 app persistence / checkpoint 的 DSN（`ragent` 仍走 `DATABASE_URL`）—— 受信任
  代码层，不在攻击面。
- **不**在生产用 `PASSWORD ragent_readonly` 明文——本 plan 仅交付 SQL 脚本与 dev 默认，
  密钥管理走部署侧（环境变量 / vault / secrets manager）。
- **不**收紧 `ragent_readonly` 到「只能查某几张表」——业务侧 query 自由 JOIN dim_/fact_
  表格，列级/行级控制后续按需。
- **不**对历史 NULL user_id 行做迁移（沿用前一个 plan 的安全优先决策）。
- **不**新增 CHECK / RLS 策略——本次只解决执行主体权限。