# Bug Review — 2026-07-30

> 状态: 只读评审

> **Source plans reviewed**：[2026-07-30-backend-async-refactor.md](2026-07-30-backend-async-refactor.md)、[2026-07-30-conversation-context-system.md](2026-07-30-conversation-context-system.md)、[2026-07-30-confirmed-exec-three-state.md](2026-07-30-confirmed-exec-three-state.md)。
>
> **Follow-up plans fixing the bugs here**：[2026-07-30-query-execution-safety-and-reporting.md](2026-07-30-query-execution-safety-and-reporting.md)（B-2/B-7/B-3）、[2026-07-30-cross-agent-state-fix.md](2026-07-30-cross-agent-state-fix.md)（B-1 ~ B-9 部分覆盖）。

不修改任何代码，仅汇报三份 plan 中枚举的 bug 真伪，并补遗其它未列入 plan 的真实问题。按严重度排序，给文件:行号、复现路径、修复方向。

---

## Plan 内枚举 bug 的核实结果

### 1. `backend-async-refactor.md`（15 条）

| # | Plan 表述 | 真伪 | 实际情况 |
|---|---|---|---|
| 1 | `llm.py:73 llm.invoke()` sync，1-5s 阻塞 | **✓ 属实** | line 73 准确。但 LangGraph `coerce_to_runnable` 自动用线程池跑 sync 函数（empirical: 1s sleep 只占 1/21 heartbeat），**不是 P0 事件循环阻塞，P1 线程池耗尽风险** |
| 2 | `sql_tools.py:130 execute_sql()` sync psycopg2 | **✓ 属实** | line 130 准确。**真正问题是 `_get_pg_conn()` 每次新建连接（lines 112/127）+ `validate_sql`+`execute_sql` 串行 = 2 条连接/次 × 重试次数**，高并发会撞连接池上限 |
| 3 | `sql_tools.py:98 validate_sql()` sync psycopg2 EXPLAIN | **✓ 属实** | line 98 准确，P1 |
| 4 | `sql_graph.py` 7 个 node 全是 `def` sync | **~ 部分属实** | **真实只有 6 个注册的 node**。`_intent_analyze` 不是图节点，是被 `parent_graph.py:213-222` 直接调用的 sync 函数——这正是 #8 bug 的成因。Plan 的 Detail C 表把它列为「node 转 async」是个口径错误 |
| 5 | `data_graph.py` 3 个 node 全 sync | **✓ 属实** | 都是 sync，但纯 CPU（关键词搜索 + dict 拼装），无 I/O。**P2，不是 P0** |
| 6 | `report_graph.py` 3 个 node 全 sync | **✓ 属实** | P1 不是 P0。`_run_step` 里有**隐藏陷阱**：`return _build_output(state)`（line 74），转 async 后会返回未 await 的 coroutine 对象，状态字典会被污染。需要改成 `return await _build_output(state)` |
| 7 | `requirement_analysis_graph.py:55,95` sync | **✓ 属实** | 实际行号 56 / 96（装饰器在那两行），node 函数本体在 55 / 95。P1 |
| 8 | `parent_graph.py:222` sync `_intent_analyze()` 从 async node 直调 | **✓✓ 这是真 P0** | sync 函数被 async node 直接 `await ... `是阻塞事件循环（无线程池包装）。Plan 的判断完全正确，**#8 是该 plan 中唯一真正的 P0 event-loop block** |
| 9 | `trace/sdk.py:92-103` 3 个 `except: pass` 吞 observability | **✓ 属实** | 行号准确。但这只是表层——见下方 #R-1，trace 模块还有更严重的问题 |
| 10 | `llm.py:43,101` 2 个 `except: pass` | **✓ 属实** | line 43 是 `register_all_tools` 失败静默；line 101 是 LLM call 计数失败静默。P1 |
| 11 | `parent_graph.py:147,266,447` 3 个 `except: pass` | **✓ 属实** | P1 |
| 12 | `memory.py:61,71` 2 个 `except: pass` | **✓ 属实但可忽略** | **整个 `app/memory.py` 是 dead code**，没有任何 import 器使用（parent_graph 用的是 `infra/memory/MemoryManager`）。改它纯属浪费时间——直接删文件 |
| 13 | `report_graph.py:128` chart parse 失败静默 | **✓ 属实，但病根不同** | `except (json.JSONDecodeError, Exception)` 里的 `Exception` 覆盖了 JSONDecodeError，所以**任何异常都被吞，不只是 parse 错**。Plan 给的修复「改成 `except json.JSONDecodeError`」会让 `TypeError` 等异常开始传播——**不是 drop-in 替换** |
| 14 | `main.py:14` 模块级 sync `load_dotenv()` | **✗ 不是 bug** | 在文件第 15 行（off-by-one），但**不是 bug**：`load_dotenv` 在 import 期跑、远早于任何 event loop，没有事件循环可阻塞 |
| 15 | `main.py:977` sync `openpyxl.Workbook()` 在 async 端点 | **✓ 属实，**P1 不是 P2** | line 977 准确。FastAPI `async def export_report_xlsx` 直接调 `Workbook()` + `wb.save()`，**这是真实的事件循环阻塞**（async 端点内没有线程池回退）。讽刺的是 plan 把这标 P2 + 暂缓，但实际严重度应该跟 #8 同档 |

**Plan 总结**：
- 12/15 属实、1 不是 bug（#14）、1 行数错（#12 实际行）、1 严重度系统性高估
- **7 个 P0 实际是 P1**，**#8 和 #15 才是真 P0 事件循环阻塞**
- **真正重要的 2 个新发现没在 plan 里**：trace `_local` 污染（见 #R-1）+ LLM call 计数被乘数（#R-2）

### 2. `conversation-context-system.md`（7 个事实声称）

| 声称 | 真伪 | 实际情况 |
|---|---|---|
| LLM 无状态、不注入历史 | **✓ 属实** | 6 个文件中 5 个（sql_graph / requirement_parser / confirmed_execution_graph / report_graph / parent_graph 的 4 个核心 node）确认无历史注入 |
| `app.conversations` 持久化消息 | **✓ 属实** | save_message / get_messages / list_sessions 都存在 |
| 列结构 `role/content/message_type/metadata` | **✓ 属实** | init_pg.sql:25-34 完全匹配 |
| 4 列 `digest*/mid_digest` 不存在 | **✓ 属实** | `agent.session` 现有列不含这 4 个 |
| `MemoryManager.recall()` 和 `UserMemory.save()` 存在 | **✓ 属实** | memory_manager.py + user_memory.py 都定义完整 |
| L3 排序权重 0.6/0.2/0.1/0.1 | **✓ 属实（小偏差）** | user_memory.py:183-198 实现确实是这 4 个权重，但 `freq` 实际是 `min(log(1+access_count)/10.0, 1.0)` 而非原始公式——plan 简化了 |
| 三个新图未接 MemoryManager | **~ 部分属实** | sql_graph / confirmed_execution_graph / requirement_analysis_graph 确实不用；但 **parent_graph.py 已经用**（lines 13, 145-146, 259-260, 438-447），plan 应修正措辞 |

**额外发现**：
- `parent_graph.memory_context` 字段在 `_classify_intent` 被填充（line 153），**下游没有任何 node 读它**——dead state
- `parse_requirement` 接 `prior_card` 参数但不注入 LLM prompt（line 77 只 `format(user_query, schema_text)`）
- L3 weight 的频率项 `min(...,1.0)` 上限是 plan 漏写的安全细节

### 3. `confirmed-exec-three-state.md`（本期实施，已落地，事后验证）

| 声称 bug | 现状（commit 56fb0fa） |
|---|---|
| 零行误判 FAILED | ✓ 已修：`_confirmed_report_agent` 三态分离（SUCCESS / EMPTY / FAILED） |
| FAILED 不入库 | ✓ 已修：`_route_after_report` 全部走 `persist_report`；新增 `persist_empty_run` + `persist_error_run` |
| SSE error 笼统文案 | ✓ 已修：`_build_sse_error` helper 带 kind + 中文 message + tried SQL |
| 前端 footer 写死 | ✓ 已修：EMPTY 改「查询已执行 · 未匹配到数据」 |

**部分落地但有残留**（下面 #B-2）：
- `sql_graph._build_output` 写 `status='FAILED' if has_error else 'SUCCESS'` 时**没有 EMPTY 分支**，而 `Literal['SUCCESS','FAILED','EMPTY']` 已经允许 EMPTY。**EMPTY 这一枚举是死代码**——confirmed_execution_graph 改成「重新从 rows 推导 verdict」绕过去了，但 `query_result.status` 字段始终只有 SUCCESS / FAILED，没有 EMPTY。这一层语义不一致是个埋伏

---

## Plan 之外发现的真实 bug（按严重度排序）

### B-1 [CRITICAL] Dev JWT 密钥 + 默认管理员账号 → 误部署即远程绕过

**文件**：`backend/app/infra/auth/jwt.py:8`、`backend/app/infra/auth/repository.py:8-9`

```python
JWT_SECRET = os.getenv("JWT_SECRET", "reportagent-dev-secret-key-change-in-production")
DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "admin")
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "admin123")
```

CLAUDE.md 注释「outside local development 显式设置」是愿望不是执行机制。任何 fresh 生产部署如果复制 `.env.example` 不改 → admin/admin123 + 公开密钥 → 远程完全绕过。

**修复方向**：`lifespan` 启动时拒绝：
- `JWT_SECRET` 等于开发字面值或短于 32 字节
- `DEFAULT_PASSWORD` 等于开发字面值时拒绝创建默认用户

### B-2 [HIGH] `sql_graph._build_output` 永远不会写 EMPTY + 错误覆盖 `row_count`

**文件**：`backend/app/agent/sql_graph.py:457-464`

```python
qr = QueryResult(
    ...
    row_count=len(result_data.get("rows", [])),   # ← 丢弃了真正的 total
    status="FAILED" if has_error else "SUCCESS",  # ← 没有 EMPTY 分支
)
```

两个 bug 在同一块：
1. **三态枚举的 `EMPTY` 永远不会被这一层写入**。Plan 让 parent 层从 rows 反推 verdict 绕过去了——但 `QueryResult.status` 在 SQL 子图内部仍然是 2 态。下游任何用 `query_result.status == 'EMPTY'` 判定零行的代码都会无声失效
2. **`row_count = len(rows)`**——`execute_sql` 在 `sql_tools.py:157` 已经填好真实 total（CTE `count(*)`）。这一行用 `len(rows)` 覆盖掉。**截断场景**：50000 行 → `truncated=True` 但 `QueryResult.row_count=5001`。LLM 在 `_plan_analysis` 看到「行数: 5001」→ 错误估算分析规模；`query_snapshot.row_count` 入库是 5001，`/export.xlsx` 也只导出 5000 行（这一轮没破是因为 xlsx 端直接读 query_snapshot，但语义错位）

**触发**：零行结果（bug 1）+ 任何 >5000 行结果（bug 2）

**修复方向**：保留 `result_data["row_count"]`，加 `if not rows and not has_error: status="EMPTY"` 分支

### B-3 [HIGH] MemorySaver 每个请求重建 + trace_id 不在子图 state 上 → observability 污染

**文件**：
- `backend/app/main.py:677, 773` (`build_confirmed_execution_graph()` / `build_requirement_analysis_graph()` 在每个请求里调)
- `backend/app/agent/sql_graph.py:66` (`SQLAgentState` TypedDict)
- `backend/app/agent/data_graph.py` (类似)
- `backend/app/agent/report_graph.py:17` (类似)
- `backend/app/infra/trace/sdk.py`

每个 chat/confirm 请求都会重建 `StateGraph(...).compile(checkpointer=MemorySaver())`。MemorySaver 是进程内 dict，**新实例为空**——所以 `config={"configurable":{"thread_id": session_id}}` 永远找不到 checkpoint。

更严重的副作用：**三个子图的 `SQLAgentState` / `DataAgentState` / `ReportAgentState` TypedDict 都没有声明 `trace_id` 字段**。LangGraph 对未声明 key 默认丢弃。Empirical 验证：

```
node saw trace_id = '<MISSING>'
node saw keys     = ['user_query']
```

后果：
- 所有子图 span 调用 `get_tracer("")`，写进同一个共享的 `_local[""]` 桶
- **多请求之间 trace 数据交叉污染**
- `main.py` 只 flush 真实 trace_id，subgraph 的 trace 永不落库
- 进程内 `_local` dict **无上限增长**——内存泄露

**修复方向**：
- `SQLAgentState` 加 `trace_id: str`、`graph.ainvoke({..., "trace_id": uuid()})`
- MemorySaver 改 `PostgresSaver`（CLAUDE.md `requirement_analysis_graph.py:168` 已经标注）
- 或把 trace 改 `ContextVar`，子图节点从 context 读

### B-4 [MEDIUM] sync `openpyxl.Workbook()` 在 `async def` 端点——真事件循环阻塞

**文件**：`backend/app/main.py` `_build_sse_error` 之后的 `/export.xlsx` 端点

`Workbook()` + `wb.save(buf)` 同步跑在 `async def` FastAPI handler 里。FastAPI 不像 LangGraph 一样自动线程池化 sync 调用（这点 #R-3 里详细解释），所以这个 `async def` 端点**真的阻塞事件循环**。一个慢导出期间整个 process 的 `/api/v1/chat` / `/confirm` 都停摆。

讽刺的是 backend-async-refactor.md 把这标 P2 + 暂缓，但实际严重度应与 B-3 同档（真事件循环阻塞）。

**修复方向**：`await asyncio.to_thread(write_xlsx_to_bytes, ...)`，或同步改 `def`（FastAPI 会对 sync 端点开线程）

### B-5 [MEDIUM] `_chat_confirmed_execution.done` 强制 final_phase=report_ready，错误卡片会被翻转消失

**文件**：`backend/app/main.py`

`/chat` 与 `/confirm` 的 SSE `done` 事件硬编码 `{"final_phase": "report_ready"}`，但前一个事件可能是 `error`。Reducer 看到 `phase/received → report_ready` 把 state.phase 翻回 `report_ready`，`canRetryFailedAction` 失效，ErrorCard 卸载——用户看到错误 toast 但主面板又显示准备就绪。

**修复方向**：镜像 `_chat_requirement_analysis` 的 `phase if "phase" in dir() else "error"` 保护

### B-6 [MEDIUM] `EmbeddingService()` 默认构造时忽略 `EMBEDDING_DIM` 环境变量

**文件**：`backend/app/embedding/service.py:12-23`、`backend/app/main.py:54, 142`

`main.py` 的 startup 检查 `VECTOR_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))` vs `embedder.embed("test")` 的实际维度——但 `get_embedder()` 用默认 `EmbeddingService(dimension=1536)` 构造，**不读 env**。操作员改 `EMBEDDING_DIM=1024` 匹配 PG `vector(1024)` 没用，启动检查仍报 `actual_dim=1536 != 1024`，信息提示「Update init_pg.sql or VECTOR_DIM」**两者都不会修好**——因为硬编码在 default。

**修复方向**：`get_embedder()` 读 `EMBEDDING_DIM` 传给 `EmbeddingService(dimension=VECTOR_DIM)`

### B-7 [MEDIUM] `MAX(version)+1` 在 READ COMMITTED 下竞态——UNIQUE 捕获但无重试 → 500

**文件**：
- `backend/app/infra/db/report_version_repository.py:41-44`
- `backend/app/infra/db/requirement_repository.py:30-33`
- `backend/app/services/report_version_service.py:194-195`

两个并发 `/confirm`（同 session_id）可能都算出 `MAX+1 = 2`，后一个 INSERT 撞 `UNIQUE(session_id, version)` → `VersionConflictError` → service 层不重试直接抛 → 用户 500。CLAUDE.md 注释说「UNIQUE constraint 序列化」**不准**——约束是报错，不是等待。

**修复方向**：
- 单语句：`INSERT ... SELECT COALESCE(MAX(version), 0) + 1 ... FROM ... WHERE session_id = $1 RETURNING version`（持有 index row lock）
- 或 service 层 `try/except VersionConflictError` 退避重试

### B-8 [MEDIUM] `_chat_legacy` `_agent` 模块全局 + 共享 MemorySaver → 并发请求可竞争

**文件**：`backend/app/main.py:177`、`backend/app/agent/parent_graph.py:542`

`_agent = build_parent_graph()` 在 lifespan 一次性构建，含 `MemorySaver()`。所有 legacy `/api/v1/chat?mode=legacy` 请求共用同一 MemorySaver。**同一 session_id 的两个并发请求**会让 `update_state(chosen_tool)` 互相覆盖。LangGraph 自身有锁，但锁窗口比直觉小。

**修复方向**：每个请求 `build_parent_graph()`，或换 `PostgresSaver`

### P-1 [LOW] `canRetryFailedAction` 漏掉 `failed_action='new' | 'supplement'`

**文件**：`frontend/src/stores/analysisReducer.ts:64-73`

只允许 `'confirm' | 'sql'`。`REQUIREMENT_INCOMPLETE` 的错误事件会以 `failed_action='new'` 出现——后端标 `recoverable: true`，前端却不显示重试按钮。

**修复方向**：白名单加 `'new' | 'supplement' | 'adjust'`

### P-2 [LOW] 401 无自动登出/重定向拦截器

**文件**：`frontend/src/api/sessionsClient.ts:22`、`analysisClient.ts:78`、`confirmStream.ts:50`

`getAuthToken()` 从 localStorage 读，过期时后端 401。客户端仅 toast「URL failed: 401」不跳登录——应用带着过期 token 继续渲染陈旧数据。

**修复方向**：在 `jsonFetch` 加 401 interceptor → `auth.logout()` + `navigate('/login')`

### P-3 [LOW] P-5-style：`CancelledError` 不被 `except Exception` 捕获 → 断开连接后 phase 卡住

**文件**：`backend/app/main.py` 多处 try/except

`asyncio.CancelledError` 是 `BaseException` 子类，Python 3.8+ `except Exception` 不抓。客户端断开后 `session_manager.update_phase(...)` 在 except 块里不跑，session 永远停在 `phase='generating'`。

**修复方向**：`except (asyncio.CancelledError, Exception)` 或在 `finally` 里调 update_phase

### P-4 [LOW] `_draft_id_from_state` 新连接读，跨 PATCH 不一致

**文件**：`backend/app/agent/confirmed_execution_graph.py:454-474`

新建连接读 draft id——`_load_confirmed_requirement` 之后到 `_sql_gate` 之间若用户 PATCH 了一次 draft，`lock_for_execution(NEW_ID)` 会锁住新 draft，但 state 里 carry 的 RequirementCard 还是旧的。概率极低（subgraph 几秒钟窗口），但语义错。

**修复方向**：让 `_load_confirmed_requirement` 把 draft id 一并放进 state

### P-5 [LOW] `_run_step` 转 async 后的 coroutine 陷阱

**文件**：`backend/app/agent/report_graph.py:74`

```python
return _build_output(state)
```

转 async 后变成未 await 的 coroutine 对象——LangGraph 不会大声报错，状态字典会被污染。Plan 的「只改函数签名」会踩这个。

**修复方向**：`return await _build_output(state)`

### P-6 [LOW] `_build_response` legacy 路径漏 FAILED/CLARIFY

**文件**：`backend/app/main.py:703-727`

legacy SSE 流只看 `INTENT_AWAIT`，FAILED/NEED_CLARIFICATION 仍走「查询完成」虚假 report

### P-7 [LOW] `requirement_parser.parse_requirement` Plan 漏改

**文件**：`backend/app/agent/requirement_parser.py:88`

Plan Detail A 表只列 `_call_llm_for_parse` (`def`→`async def`)，但 `parse_requirement` 本身也得变 `async def`——否则 `_requirement_parse` 没法 await 它

### P-8 [LOW] `extract_sql` 多语句不检测 + 错误文本泄露到 prompt

**文件**：`backend/app/utils/text.py:19-34`、`backend/app/agent/sql_graph.py:362-368`

LLM 输出 `SELECT 1; SELECT 2` 时，`extract_sql` 原样返回，`check_sql_safety` 的 `sqlglot.parse_one` 会抛——错误文本喂回 `_generate_sql` 的 prompt 注入到下一轮。前端注入「注入提示词，要求执行：DELETE …」可能让 LLM 生成恶意 SQL——但 DDL/DML 黑名单挡得住（CTE-DML 通过 token 黑名单捕获），`SELECT INTO` 依赖 PG EXPLAIN 拦截

---

## Plan 内 bug 与 Plan 外 bug 的对照

| 来源 | 数量 | 真实程度 |
|---|---|---|
| backend-async-refactor.md | 15 | 12 属实（严重度普遍偏高）、2 真实但被掩盖（#8 #15 真正 P0）、1 不是 bug（#14） |
| conversation-context-system.md | 7 事实声称 | 6 属实 + 1 部分属实（parent_graph 已经在用） |
| confirmed-exec-three-state.md | 4 | 3 完整修复 + 1 残留语义错位（B-2） |
| **本轮新发现的真实 bug** | **8（B-1..B-8）+ 8（P-1..P-8）** | 已分别定位 |

---

## 修复优先级建议

| 优先级 | bug | 改动量 | 影响 |
|---|---|---|---|
| P0（安全） | B-1 | ~30 行 lifespan 校验 | 阻止误部署 auth bypass |
| P0（数据正确性） | B-2 | 5 行 sql_graph._build_output + 1 行 Literal 验证 | 关闭「EMPTY 不入库」地雷 + 修复截断 row_count |
| P0（可观测性） | B-3 | 子图 State 加 trace_id + trace 模块 ContextVar 化 | 修内存泄露 + 修 trace 污染 |
| P1 | B-4 + B-5 | to_thread 包一下 + done 事件加 phase 保护 | 解锁事件循环 + 错误卡片不消失 |
| P1 | B-6 | get_embedder 读 env | 允许 EMBEDDING_DIM 真的生效 |
| P1 | B-7 | 单语句 INSERT...SELECT 或重试循环 | 解锁并发 confirm |
| P2 | B-8 + P-1..P-8 | 各自独立 | 已知角落 |

---

## 明确不做（本轮）

- 任何代码改动（本次只 review）
- 落实 plan 中建议的修改路径——上面给的「修复方向」是建议，不是补丁
- 自动触发任何「修 #N」动作等用户点头
- 不展开为可执行 plan——这是 review，下一步是否开 `2026-07-30-fix-<topic>.md` 由用户决定
