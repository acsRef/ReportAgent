# 2026-07-30 Bug 修复完成报告

> 状态: 已完成（本轮修复完成报告）

> **用途**：记录本轮「把今天 plan 里的 bug 都修复」的实际落地结果——已完成项、关键取舍、残留项、验证结论。
> **关联**：[2026-07-30-index.md](2026-07-30-index.md)（总索引）、[2026-07-30-bug-review.md](2026-07-30-bug-review.md)（B-1~B-8 / P-1~P-8 来源）、[2026-07-30-cross-agent-state-safety.md](2026-07-30-cross-agent-state-safety.md)（C-1~C-9 来源）、[2026-07-30-query-execution-safety-and-reporting.md](2026-07-30-query-execution-safety-and-reporting.md)（层 2/6/7）。
> **范围决策**：用户选定「修全部 bug」，但经核对 `bug-review.md` 勘误后，**全量 async 重构停在安全子集**（见下「残留项」）。

## 一、验证结论（先说结果）

| 项 | 结果 |
|---|---|
| 后端 pytest（smoke/contracts/graphs/persistence） | **107 passed, 1 skipped**（e2e 需 `REPORTAGENT_E2E`，正常跳过） |
| 前端 vitest | **242 passed**（39 文件） |
| 前端 `tsc -b` / `oxlint` | 均 clean |
| 后端启动冒烟（uvicorn + 真 PG） | 3s 起，`/health` ok，admin 登录返回 JWT，embedding 维度校验 1536==1536，整条 lifespan 链路正常 |

基线对照：开工前后端 90 passed / 前端 241 passed；本轮新增 17 个后端用例 + 1 个前端用例并全部转绿，无回归。

## 二、已完成项（按来源编号）

### 安全
| 编号 | 内容 | 落点 | 测试 |
|---|---|---|---|
| B-1 | auth 启动闸，**fail-closed**：`APP_ENV` 未设置按 production；非开发环境拒绝默认 `JWT_SECRET`/弱密钥/`admin123` | 新增 `app/infra/auth/startup_guard.py`；接入 `main.py` lifespan 最前部；`.env.example` 增 `APP_ENV`/`ALLOW_INSECURE_DEFAULT_AUTH` | `tests/test_auth_startup_guard.py`（10 用例） |
| B-1 后门 | 已存在的弱密码 admin 升生产也拒绝启动 | `app/infra/auth/repository.py` `ensure_default_user` | 同上（逻辑覆盖） |

### 数据正确性
| 编号 | 内容 | 落点 | 测试 |
|---|---|---|---|
| B-2 / 层2 / legacy-Bug2 | `_build_output` 三态：补 EMPTY 分支、保留 CTE 真实 `row_count`、`truncated`、`error_kind` 透传 | `app/agent/sql_graph.py` | `tests/graphs/test_sql_graph_output.py` |
| legacy-Bug1 | 重试反馈喂回 **execute 阶段**错误（此前 validate 通过但 execute 失败时错误被丢弃） | `app/agent/sql_graph.py` `_generate_sql` | 同上 |
| B-7 / 层6 | `MAX(version)+1` 并发竞态 → 事务级咨询锁 `pg_advisory_xact_lock(ns, hashtext(session_id))` 串行化 | `app/infra/db/report_version_repository.py`、`requirement_repository.py` | `tests/persistence/test_version_concurrent.py`（2 用例，真 PG 8 并发） |

### 并发隔离与可观测性
| 编号 | 内容 | 落点 |
|---|---|---|
| B-3 / 层7 | 三个子图 state 声明 `trace_id`（调用点早已传入，此前被 LangGraph 静默丢弃 → span 落进共享 `_local[""]` 桶） | `data_graph.py`、`sql_graph.py`、`report_graph.py` |
| C-4 | trace `_local` fan-out 污染 + 泄露 → 引入 `ContextVar` 当前 tracer，`llm.py` 只归属当前 tracer；`flush()` 的 `_local.pop` 移入 finally + 异常加日志 | `app/infra/trace/sdk.py`、`app/llm.py` |
| C-1 / B-8 | legacy 全局 `_agent` 共享 MemorySaver 跨请求污染 → **按 session 的 asyncio.Lock** 串行化（保留共享 saver，不破坏 clarify interrupt 连续性） | `app/main.py` |
| C-2 | `clarification_history` / `current_query` 滚动窗口 + 硬上限 | `app/agent/parent_graph.py` |
| C-3 | `_format_confirmed_requirement` 各字段 + 假设区块截断 | `app/agent/confirmed_execution_graph.py` |
| C-6 | `SQLAgentState.error` 类型 `str`→`ErrorDetail`，与父图对齐；`_evaluate` 三处返回 ErrorDetail | `app/agent/sql_graph.py` |
| C-7 | `requirement_parser._schema_text` 单表描述 + 整体截断 | `app/agent/requirement_parser.py` |
| C-9 | 子图边界统一 `_validate_qr`（`model_validate`），收敛三处防御解析 | `app/agent/report_graph.py` |
| C-8 | `_format_tools_for_prompt` 按白名单缓存 | `app/llm.py` |

### 事件循环 / SSE / 配置
| 编号 | 内容 | 落点 |
|---|---|---|
| 真 P0 #1 | `parent_graph:222` async 节点直调 sync `_intent_analyze`（阻塞 event loop）→ `asyncio.to_thread` | `app/agent/parent_graph.py` |
| B-4 / 真 P0 #2 | `export.xlsx` 同步 openpyxl 阻塞 event loop → 抽 `_build_xlsx_bytes` 经 `asyncio.to_thread` | `app/main.py` |
| B-5 | `done` 事件 `final_phase` 硬编码 `report_ready` → 跟踪真实结局，出错时发 `error` | `app/main.py`（adjust + confirm 两条流） |
| B-6 | `get_embedder()` 忽略 `EMBEDDING_DIM` → 读 env 传入 | `app/embedding/service.py` |
| P-3 | `CancelledError`（BaseException）不被 `except Exception` 捕获 → 客户端断连后 phase 卡死；增加 `except asyncio.CancelledError` best-effort 标记 + 重抛 | `app/main.py`（adjust + confirm） |
| P-6 | legacy 失败不再走 `_build_response` 发假「查询完成」→ 发结构化 `error` 事件 | `app/main.py` |
| Detail D | 多处 `except: pass` → `logger.warning`（注册失败、memory 召回/记忆、trace 落库） | `app/llm.py`、`app/agent/parent_graph.py`、`app/infra/trace/sdk.py` |

### 前端
| 编号 | 内容 | 落点 | 测试 |
|---|---|---|---|
| P-1 | `canRetryFailedAction` 白名单补 `new`/`supplement`/`adjust`（REQUIREMENT_INCOMPLETE 可恢复错误此前不显示重试） | `src/stores/analysisReducer.ts` | `analysisReducer.test.ts`（改正向/反例） |
| P-2 | 统一 401 处理：登出 + 跳登录，避免带过期 token 渲染陈旧数据 | 新增 `src/api/unauthorized.ts`；接入 `sessionsClient`/`analysisClient`/`confirmStream` | 现有套件 |

## 三、关键设计取舍

1. **B-1 选 fail-closed**（用户拍板）：`APP_ENV` 未设置按 production。本地 `.env` 已补 `APP_ENV=development` + `ALLOW_INSECURE_DEFAULT_AUTH=1`，本地开发不受影响；生产照抄 `.env.example` 忘配会**显式启动失败**而非隐性漏洞。
2. **B-7 用咨询锁而非单语句 INSERT**：READ COMMITTED 下单纯 `INSERT...SELECT MAX+1` 仍有残窗；`pg_advisory_xact_lock` 在调用方事务内真正串行化。report_version / requirement_draft 用命名空间 1/2 分开，互不阻塞。
3. **C-1 用 per-session 锁而非 per-request 重建**：per-request 重建会破坏 legacy clarify 的跨请求 interrupt 连续性；锁既消除并发污染又保住 interrupt。彻底方案是 PostgresSaver（独立 PR）。
4. **C-4 + 层7 组合**：层7（state 声明 trace_id）修「子图 span 落错桶」；ContextVar 修「LLM 调用 fan-out 到所有 tracer」。该组合对 sync 节点跑在执行器线程的场景也正确（set 发生在线程内 wrapper 入口）。
5. **全量 async 停在安全子集**：见残留项第 1 条。

## 四、残留项（明确未做 + 原因）

| 项 | 原因 |
|---|---|
| **全量 async 重构**（`call_llm→ainvoke`、`sql_tools` psycopg2→asyncpg、13 node sync→async、`parse_requirement` async） | 经用户确认停在安全子集。`bug-review.md` 勘误已将其降为 P1：真正的 event-loop 阻塞全项目仅 2 处，均已用 `asyncio.to_thread` 修掉；其余仅是线程池/socket 压力（LangGraph 会自动线程池化 sync 节点）。全量改造需连带重写 5+ 个直接调用同步函数的测试文件，coroutine 陷阱多、边际收益低。 |
| **C-5 PG 连接池**（psycopg2 每次新连接的 socket 风暴） | 与 async 重构的方案 B（asyncpg）绑定，随 async 一起做。当前仍每次新建 psycopg2 连接（已有 `connect_timeout`/`statement_timeout`）。 |
| **conversation-context-system**（分层对话上下文 L1/L2/L3） | 新特性，非 bug，本轮不做。 |
| **PostgresSaver 替代 MemorySaver** | 独立 PR（CLAUDE.md 已标注的生产改进）。 |
| **P-4**（`_draft_id_from_state` 跨 PATCH 窗口不一致） | LOW，窗口极短、概率极低。 |
| **P-5**（`_run_step` 转 async 的 coroutine 陷阱） | 仅在未来转 async 时触发；当前 sync 无影响，随 async 处理。 |
| **P-7**（`parse_requirement` async） | 随 async 重构。 |
| **P-8**（`extract_sql` 多语句检测） | LOW；DDL/DML 黑名单 + sqlglot + EXPLAIN 三层安检已挡住主要注入面。 |
| **`app/memory.py` 死代码删除** | `bug-review` 认定无引用；可独立清理，本轮未动。 |

## 五、改动文件清单

后端：`app/infra/auth/startup_guard.py`(新)、`app/infra/auth/repository.py`、`app/main.py`、`app/llm.py`、`app/embedding/service.py`、`app/infra/trace/sdk.py`、`app/infra/db/report_version_repository.py`、`app/infra/db/requirement_repository.py`、`app/agent/sql_graph.py`、`app/agent/data_graph.py`、`app/agent/report_graph.py`、`app/agent/parent_graph.py`、`app/agent/confirmed_execution_graph.py`、`app/agent/requirement_parser.py`
后端测试：`tests/test_auth_startup_guard.py`(新)、`tests/graphs/test_sql_graph_output.py`(新)、`tests/persistence/test_version_concurrent.py`(新)
前端：`src/api/unauthorized.ts`(新)、`src/api/sessionsClient.ts`、`src/api/analysisClient.ts`、`src/api/confirmStream.ts`、`src/stores/analysisReducer.ts`、`src/stores/__tests__/analysisReducer.test.ts`
配置/文档：`.env`(本地，补 APP_ENV)、`backend/.env.example`、本文件

## 六、明确不做（本报告）

- 不 commit / 不 push（等用户示意）。
- 不动上述「残留项」——每项都有独立归属（async 重构 / 独立 PR / 新特性）。
- 不复述各 plan 的实现细节——见对应 plan 与本文件第二节落点。
