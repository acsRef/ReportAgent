# Plan: Backend Async Refactor — 消除同步阻塞

## Context

整个后端存在严重的 **sync-in-async 反模式**。核心问题：

1. 三个子图（`sql_graph`、`data_graph`、`report_graph`）的 **全部 13 个 node 都是 `def sync`**，但父图用 `graph.ainvoke()` 调它们。LangGraph 在 event loop 上直接跑 sync 函数，LLM HTTP 请求（1-5s）和 psycopg2 SQL 执行（长至 30s）**阻塞整个事件循环**。

2. 同步 psycopg2 在 async 栈里做 DB I/O，高并发下等效单线程串行。

3. `except: pass` 广泛存在，observability flush 失败、memory 层故障、LLM 调用记录失败——全部静默丢弃，监控告警黑盒。

4. `call_llm()` 是 sync，但所有 LLM 调用都经过它。这是阻塞的**最大单一来源**。

### 问题清单（按影响排序）

| # | 位置 | 问题 | 类型 |
|---|------|------|------|
| 1 | `app/llm.py:73` | `llm.invoke()` 是 sync HTTP，1-5s 阻塞 | P0 阻塞 |
| 2 | `app/tools/sql_tools.py:130` | `execute_sql()` 用 sync psycopg2，最长 30s | P0 阻塞 |
| 3 | `app/tools/sql_tools.py:98` | `validate_sql()` 用 sync psycopg2 EXPLAIN | P0 阻塞 |
| 4 | `app/agent/sql_graph.py` | 全部 7 个 node 是 `def` sync | P0 阻塞 |
| 5 | `app/agent/data_graph.py` | 全部 3 个 node 是 `def` sync | P0 阻塞 |
| 6 | `app/agent/report_graph.py` | 全部 3 个 node 是 `def` sync | P0 阻塞 |
| 7 | `app/agent/requirement_analysis_graph.py:55,95` | `_security_guard`、`_requirement_parse` 是 sync | P0 阻塞 |
| 8 | `app/agent/parent_graph.py:222` | sync `_intent_analyze()` 从 async node 直接调 | P0 阻塞 |
| 9 | `app/infra/trace/sdk.py:92-103` | 3 个 `except: pass` 吞 observability 错误 | P1 吞异常 |
| 10 | `app/llm.py:43,101` | 2 个 `except: pass` 吞注册/记录错误 | P1 吞异常 |
| 11 | `app/agent/parent_graph.py:147,266,447` | 3 个 `except: pass` 吞 memory 错误 | P1 吞异常 |
| 12 | `app/memory.py:61,71` | 2 个 `except: pass` 吞 mem0 错误 | P1 吞异常 |
| 13 | `app/agent/report_graph.py:128` | `except: pass` 吞 chart parse 错误 | P1 吞异常 |
| 14 | `app/main.py:14` | 模块级 sync `load_dotenv()` | P2 |
| 15 | `app/main.py:977` | sync `openpyxl.Workbook()` 在 async 端点 | P2 |

## Design

### 改造原则

1. **不改 Python/Node 数量**——只改函数签名，不改图拓扑
2. **不改数据流向**——`sql_tools.py` 的返回格式、`call_llm()` 的调用方、`traced_node` 装饰器行为、graph state shape——全部保持向后兼容
3. **分层改造**——从最底层（llm、sql_tools）向上改，避免同一层改两次
4. **所有 `except: pass` 至少加 `logger.warning`**，关键路径改 `logger.error` 或 `raise`

### 改造路径（从底向上）

```
第三层（6 → 0）
  sql_graph, data_graph, report_graph, parent_graph
  所有 node: def → async def

第二层（6 → 0）
  llm.call_llm          → async def (await llm.ainvoke)
  sql_tools.execute_sql → async def (asyncpg 替代 psycopg2)
  sql_tools.validate_sql→ async def (asyncpg Explain)

第一层（6 → 1）
  sql_tools.py   → async def, asyncpg
  llm.py         → async def, llm.ainvoke
  traced_node    → 无需改动（已同时支持 sync/async）
  trace/sdk.py   → except 加 logger.warning
  parent_graph.py→ except 加 logger.warning
  memory.py      → except 加 logger.warning
  report_graph.py→ except 加 logger.warning
```

---

### Detail A: `app/llm.py` — 转 async（影响最大）

**改动：**

```python
# 改前
def call_llm(prompt: str | list, **kwargs) -> str:
    llm = get_chat_llm(**kwargs)
    resp = llm.invoke(prompt)               # sync HTTP, 1-5s 阻塞
    ...

# 改后
async def call_llm(prompt: str | list, **kwargs) -> str:
    llm = get_chat_llm(**kwargs)
    resp = await llm.ainvoke(prompt)        # async HTTP, 释放 event loop
    ...
```

**影响范围（全部要改成 `await call_llm()`）：**

| 文件 | 行数 | 调用方 |
|------|------|--------|
| `app/agent/sql_graph.py` | 161, 292, 370 | `_intent_analyze`(sync→async), `_plan`(sync→async), `_generate_sql`(sync→async) |
| `app/agent/report_graph.py` | 57 | `_plan_analysis`(sync→async) |
| `app/agent/requirement_parser.py` | 78 | `_call_llm_for_parse`(sync→async) |
| `app/agent/parent_graph.py` | 483 | `_clarify`(sync→async) |

**`_format_tools_for_prompt` 的两个 `except`：**
- 第一个（line 43）改为 `logger.warning("tool registration failed: %s", exc)`
- 第二个（line 59）已有 `logger.warning`，不动

**`call_llm` 的 observability 记录 `except`（line 101）：**
```python
except Exception as exc:
    logger.warning("failed to record LLM call: %s", exc)
```

**Pre-condition:** `ChatOpenAI` 的 `ainvoke()` 可用。langchain-openai ≥ 0.1.0 已支持。当前 requirements.txt 无版本下限，需要确认。验证：`hasattr(ChatOpenAI, 'ainvoke')`。

---

### Detail B: `app/tools/sql_tools.py` — psycopg2 → asyncpg

**改动：**

```python
# 改前
def validate_sql(sql: str) -> str:
    conn = _get_pg_conn()
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN {sql}")         # sync 阻塞
    conn.close()
    return json.dumps({"valid": True, "error": ""})

def execute_sql(sql: str) -> str:
    conn = _get_pg_conn()
    with conn.cursor(cursor_factory=...) as cur:
        cur.execute(sql)                      # sync 阻塞，最长30s
        raw_rows = cur.fetchall()
    conn.close()
    return json.dumps({...})
```

改为：

```python
# 改后
async def validate_sql(sql: str) -> str:
    pool = get_pool()                          # 复用现有的 asyncpg pool
    async with pool.acquire() as conn:
        try:
            await conn.execute(f"EXPLAIN ({sql})")  # asyncpg 原生 async
            return json.dumps({"valid": True, "error": ""})
        except asyncpg.exceptions.PostgresError as exc:
            return json.dumps({
                "valid": False,
                "error": str(exc)[:300],
                "error_kind": _classify_asyncpg_error(exc),
            })

async def execute_sql(sql: str) -> str:
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                "WITH src AS (" + sql.rstrip().rstrip(";") + ") "
                f"SELECT *, (SELECT count(*) FROM src) AS _total "
                f"FROM src LIMIT {MAX_RESULT_ROWS + 1}"
            )
            ...
        except asyncpg.exceptions.PostgresError as exc:
            return json.dumps({"error": str(exc)[:300], "error_kind": ...})
```

**需要新增 `_classify_asyncpg_error`：** 映射 asyncpg 异常类到 6 类 error_kind，替代现有的 `_classify_psycopg2_error`。

**调用方全部改成 `await`：**

| 文件 | 行 | 调用 |
|-----|----|------|
| `app/agent/sql_graph.py:386` | `_validate` | `await validate_sql(sql)` |
| `app/agent/sql_graph.py:397` | `_execute` | `await execute_sql(sql)` |

**注意：** `chart_advisor` 和 `insight_analyst` 是纯 CPU 计算（解析 JSON 算统计），无 I/O，不需要改 async。

**Pre-condition:** 现有的 asyncpg pool（`get_pool()`）与 `sql_tools.py` 使用同一个 `DATABASE_URL`。已验证：`postgres.py` 的 `POSTGRES_DSN` 和 `sql_tools.py` 的 `PG_DSN` 来源相同（`DATABASE_URL` env var），可以复用。

---

### Detail C: 子图 Node 全部转 async（影响面最大，但机械）

**`app/agent/sql_graph.py` — 7 个 node：**

| Node | 当前 | 改为 | 包含 I/O |
|------|------|------|----------|
| `_intent_analyze` | def | async def | `await call_llm()` |
| `_plan` | def | async def | `await call_llm()` |
| `_generate_sql` | def | async def | `await call_llm()` |
| `_validate` | def | async def | `await validate_sql()` |
| `_execute` | def | async def | `await execute_sql()` |
| `_evaluate` | def | async def | 纯 CPU，无 I/O—但保持 async 统一 |
| `_build_output` | def | async def | 纯 CPU，同上 |

每个 node 的改动模式：
```python
# 改前
@traced_node("sql_plan")
def _plan(state: SQLAgentState) -> dict:
    prompt = f"...{state['user_query']}..."
    plan_text = call_llm(prompt, max_tokens=1500)
    ...

# 改后
@traced_node("sql_plan")
async def _plan(state: SQLAgentState) -> dict:
    prompt = f"...{state['user_query']}..."
    plan_text = await call_llm(prompt, max_tokens=1500)
    ...
```

**`app/agent/data_graph.py` — 3 个 node：**

| Node | 当前 | 改为 | I/O |
|------|------|------|-----|
| `_detect_intent` | def | async def | 纯 CPU（关键词搜索） |
| `_search_schema` | def | async def | `await search_tables.ainvoke(...)` |
| `_build_context` | def | async def | 纯 CPU |

`search_tables.invoke()` 改为 `await search_tables.ainvoke()`。`@tool` 默认支持 `.ainvoke()`（LangChain ≥ 0.1.0）。

**`app/agent/report_graph.py` — 3 个 node：**

| Node | 当前 | 改为 | I/O |
|------|------|------|-----|
| `_plan_analysis` | def | async def | `await call_llm()` |
| `_run_step` | def | async def | 纯 CPU（调 chart_advisor / insight_analyst） |
| `_build_output` | def | async def | 纯 CPU |

`_run_step` 调的 `chart_advisor()` / `insight_analyst()` 都是纯 CPU 函数，无需 await 也不需要改它们。

**`app/agent/requirement_analysis_graph.py` — 2 个 sync node：**

| Node | 当前 | 改为 | I/O |
|------|------|------|-----|
| `_security_guard` | def | async def | 纯 CPU |
| `_requirement_parse` | def | **async def** | `await parse_requirement(...)` |

`parse_requirement` → `_call_llm_for_parse` → `await call_llm()`。

**`app/agent/parent_graph.py` — sync 转 async：**

| Node | 当前 | 改为 | I/O |
|------|------|------|-----|
| `_security_guard` | def | async def | 纯 CPU |
| `_classify_intent` | **async def 已有** | 不动 | 已有 await |
| `_evaluate` | def | async def | 纯 CPU |
| `_clarify` | def | async def | `await call_llm()` |
| `_dashboard_placeholder` | def | async def | 纯 CPU |

`_clarify` line 483: `question = call_llm(prompt)` → `question = await call_llm(prompt)`。

**`_route_*` 路由函数不需要改：** 路由函数只读 state 返回字符串，无 I/O，保持 sync。LangGraph 支持 conditional edge 函数为 sync 或 async。

---

### Detail D: `except: pass` → 加日志

| 文件 | 行 | 改前 | 改后 |
|------|-----|------|------|
| `infra/trace/sdk.py:92` | `save_trace` 失败 | `except: pass` | `logger.warning("save_trace failed: %s", exc)` |
| `infra/trace/sdk.py:97` | `save_llm_call` 失败 | `except: pass` | `logger.warning("save_llm_call failed: %s", exc)` |
| `infra/trace/sdk.py:101` | `save_span` 失败 | `except: pass` | `logger.warning("save_span failed: %s", exc)` |
| `llm.py:43` | tool 注册失败 | `except: pass` | `logger.warning("register_all_tools failed: %s", exc)` |
| `llm.py:101` | LLM call 记录失败 | `except: pass` | `logger.warning("record_llm_call failed: %s", exc)` |
| `parent_graph.py:147` | memory recall 失败 | `except: memory_context = ""` | `except Exception as exc: logger.warning("memory recall failed: %s", exc); memory_context = ""` |
| `parent_graph.py:266` | memory remember_query 失败 | `except: pass` | `logger.warning("remember_query failed: %s", exc)` |
| `parent_graph.py:447` | memory remember_preference 失败 | `except: pass` | `logger.warning("remember_preference failed: %s", exc)` |
| `memory.py:61` | search_memories 失败 | `except: return []` | `logger.warning("mem0 search failed: %s", exc); return []` |
| `memory.py:71` | add_memory 失败 | `except: pass` | `logger.warning("mem0 add failed: %s", exc)` |
| `report_graph.py:128` | chart JSON parse 失败 | `except (json.JSONDecodeError, Exception): pass` | `except json.JSONDecodeError as exc: logger.warning("chart parse failed: %s", exc)` |

注意：`main.py:568` 已有 `logger.warning`，不需要改。

---

### Detail E: `app/db.py` — DuckDB sync 不阻塞（P2，暂不改）

DuckDB 只在 `lifespan` 启动时调一次，不是热路径。标注为**已知项**暂不改。

### Detail F: `app/main.py:977` — openpyxl sync（P2，暂不改）

Excel 导出在 `async def` 端点里做 CPU 密集的 `Workbook()` 创建。可以包一层 `asyncio.to_thread(write_xlsx, ...)`，但不是高频路径，暂不改。

---

### 改造顺序（依赖倒置）

```
Step 1: infra/trace/sdk.py     — except → logger.warning（零依赖）
Step 2: llm.py                 — call_llm async + except → log（需 ChatOpenAI.ainvoke）
Step 3: sql_tools.py           — validate_sql/execute_sql async + asyncpg（需 get_pool）
Step 4: data_graph.py          — 全部 node async def + search_tables.ainvoke（依赖 Step 1）
Step 5: report_graph.py        — 全部 node async def + except → log（依赖 Step 1,2）
Step 6: sql_graph.py           — 全部 node async def + await call_llm/validate_sql（依赖 Step 1,2,3）
Step 7: requirement_parser.py  — _call_llm_for_parse async（依赖 Step 2）
Step 8: requirement_analysis_graph.py — node async def（依赖 Step 1,7）
Step 9: parent_graph.py        — node async def + await call_llm + except → log（依赖 Step 1,2,3,4,5,6）
Step 10: memory.py             — except → log（零依赖，可随时做）
```

## Files to change

- `backend/app/llm.py` — `call_llm` async def + `ainvoke` + except log
- `backend/app/tools/sql_tools.py` — `validate_sql`/`execute_sql` async + asyncpg + `_classify_asyncpg_error`
- `backend/app/agent/sql_graph.py` — 全部 7 个 node sync→async + await 所有 I/O
- `backend/app/agent/data_graph.py` — 全部 3 个 node sync→async + `ainvoke`
- `backend/app/agent/report_graph.py` — 全部 3 个 node sync→async + except log
- `backend/app/agent/requirement_analysis_graph.py` — 2 个 node sync→async
- `backend/app/agent/requirement_parser.py` — `_call_llm_for_parse` async def
- `backend/app/agent/parent_graph.py` — 4 个 node sync→async + except log
- `backend/app/infra/trace/sdk.py` — 3 个 except→logger.warning
- `backend/app/memory.py` — 2 个 except→logger.warning

## Reused existing utilities

- `app/infra/db/postgres.py` `get_pool()` — 复用 asyncpg pool，sql_tools 不再自建 sync 连接
- `langchain_openai.ChatOpenAI.ainvoke()` — 原生支持 async，无需额外依赖
- `langchain_core.tools.Tool.ainvoke()` — `@tool` 默认提供 `.ainvoke()`，data_graph.py 直接可用
- `app/utils/text.py` `safe_json_parse()` — 无 I/O，不需要改
- `app/models/contracts.py` — 错误枚举不需要改，`ErrorKind` 语义不变

## Verification

### 测试策略

1. **单元测试不变**：所有 `backend/tests/smoke/`、`backend/tests/contracts/` 的用例不涉及 I/O 阻塞，不受影响
2. **Graph 测试 `-m graphs`**：需要确认 sync→async 后 graph 行为一致。核心断言（SQL gate 不通过时 call_llm 次数为 0、FAILED 路由、retry 逻辑）全部保持
3. **E2E `-m e2e`**：`test_full_flow.py` 驱动真实 API，同步转 async 后响应时间应该降低（不阻塞），而不是增加

### 回归验证

```bash
cd backend
pytest -m smoke          # 全部通过
pytest -m contracts      # 全部通过
pytest -m graphs         # 全部通过
pytest -m persistence    # 需要 PG，通过
REPORTAGENT_E2E=1 pytest tests/e2e/test_full_flow.py -s   # 全流程
```

### 性能验证

```bash
# 改前改后各跑一次并发测试
# 发送 5 个并发请求，确认改后 total elapsed 显著小于改前
curl -N -X POST ... -d '{"user_query":"2024年各区域销售额","session_id":"t1","mode":"new"}' &
curl -N -X POST ... -d '{"user_query":"今年华东趋势","session_id":"t2","mode":"new"}' &
# 改前：串行处理，总耗时 = sum(每个请求)
# 改后：并行处理，总耗时 ≈ max(每个请求)
```

## Explicitly NOT doing

- **psycopg2 完全移除**：不在本轮做。`sql_tools.py` 改 asyncpg 后 psycopg2 仍被 `requirements.txt` 引用，等下次清理依赖时移除
- **DuckDB (`app/db.py`) 转 async**：只在启动时用一次，不是热路径
- **`openpyxl.Workbook()` 转 async**：低频导出端点，`asyncio.to_thread` 后续优化
- **`traced_node` 装饰器改造**：目前已同时支持 sync/async，不用动
- **依赖注入重构**（全局 `get_pool` → 构造函数注入）：这是独立改造，不混入本 PR
- **`contextvars` 替代 trace_id 手传**：独立改造，不混入
- **`data_graph.py` 的 `_detect_intent` 无用节点移除**：虽然是 dead code 但不在本轮范围
- **Module-level `load_dotenv()`**：只在启动时执行一次，不阻塞热路径

## Outcome

- 全部 13 个子图 node sync→async
- `call_llm` 从 `invoke` → `ainvoke`，释放 event loop
- `validate_sql` / `execute_sql` 从 psycopg2 → asyncpg，复用现有 pool
- 所有 `except: pass` 至少升级为 `logger.warning`
- 高并发下后端不再被长 I/O 阻塞，吞吐量从串行变为并行
