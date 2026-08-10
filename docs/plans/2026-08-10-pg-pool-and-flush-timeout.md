# 2026-08-10 PG 连接池扩容 + Trace flush 超时保护

> 状态: 已完成（commit 见下）

## Context

用户转投的 GitHub issue「[P1] PostgreSQL 连接池优化 + Trace flush 超时保护」提出两个现实问题：

1. **连接池太小**：`backend/app/infra/db/postgres.py:20` 的 `init_pool(dsn, min_size=2, max_size=10)`。分析期 SQL（`tools/sql_tools.py` 的同步 psycopg2）、应用持久化、observability 读写、checkpoint 全部共享这一个 asyncpg 池。10+ 并发时连接排队，SQL 延迟 +100~500ms，P95 超 500ms。
2. **Trace flush 无超时**：`backend/app/infra/trace/sdk.py:122` 的 `flush()` 每个保存操作有 try/except，但**外层没有总超时**。`flush()` 在 `main.py` 的 4 处 SSE 生成器 `finally:` 块里调用（433/566/755/1036 行），DB 卡住时 flush 无限挂起，SSE `done` 事件被拖死。

现状（查证属实）：asyncpg 0.31 Pool 暴露 `get_size()` / `get_idle_size()` / `get_max_size()`，可做状态监控；`flush()` 唯一清理点是 `finally: _local.pop(...)`，异常路径已释放桶，但无总超时。

## Design

两个独立改动，各收敛一个文件。

### 1. 连接池 10 → 20 + 监控（`backend/app/infra/db/postgres.py`）

- `init_pool` 改 `max_size=20`（支持 ~15 并发 SQL + 5 缓冲），`min_size=2` 不变。
- 新增模块级后台监控任务 `_pool_monitor_task`：
  - `start_pool_monitor(interval=60.0)`：幂等创建 `asyncio.create_task(_pool_monitor_loop(interval))`。
  - `_pool_monitor_loop`：每 `interval` 秒读全局 `_pool`，若未初始化或 `is_closing()` 则跳过；否则调 `_log_pool_status(pool)`。
  - `_log_pool_status(pool)`：`size = get_size()`、`idle = get_idle_size()`、`max = get_max_size()`；当 `idle == 0 and size >= max`（池耗尽、请求在排队）打 `logger.warning`，否则 `logger.info`。抽成独立函数便于离线单测。
  - `stop_pool_monitor()`：取消并置空任务。
- `main.py` lifespan：`await init_pool()` 后调 `start_pool_monitor()`；shutdown 里 `close_pool()` 前调 `stop_pool_monitor()`。

### 2. Trace flush 10s 总超时（`backend/app/infra/trace/sdk.py`）

- 新增 `_FLUSH_TIMEOUT = float(os.getenv("TRACE_FLUSH_TIMEOUT", "10.0"))`。
- `flush()` 重构为：`finally` 兜底 `_local.pop` 不变，中间用 `asyncio.wait_for(self._flush_db(), timeout=_FLUSH_TIMEOUT)` 包住实际落库。
  - `asyncio.TimeoutError` → `logger.warning("trace flush timed out after %ss for trace_id=%s", ...)`，**不重抛**，让 SSE 主流程继续。
  - 其他异常 → `logger.warning`，不重抛（保持现状语义）。
- 抽出 `_flush_db()`：原三个 `repo.save_trace / save_llm_call / save_span` 的逐操作 try/except 原样保留（单点失败不影响整体），只是整体被 `wait_for` 包一层总超时。
- `wait_for` 超时会取消内层协程，asyncpg 连接随取消释放，不泄漏。

## Files to change

- `backend/app/infra/db/postgres.py`：`max_size=20` + 监控任务（`start_pool_monitor` / `stop_pool_monitor` / `_pool_monitor_loop` / `_log_pool_status`）。
- `backend/app/main.py`：lifespan 里接线 `start_pool_monitor()` / `stop_pool_monitor()`。
- `backend/app/infra/trace/sdk.py`：`flush()` 加 `wait_for` 总超时 + 抽 `_flush_db()`。
- `docs/plans/2026-08-10-pg-pool-and-flush-timeout.md`（本文件）。

## Reused existing utilities

- `asyncpg.Pool.get_size() / get_idle_size() / get_max_size()`：现成 API，不造轮子。
- `main.py` lifespan `init_pool() / close_pool()` 既有接线：监控任务挂在这两个点之间。
- `sdk.py` 既有的 `finally: _local.pop(self.trace_id, None)`：超时路径复用同一清理点，不新增桶管理。

## Verification

```bash
cd backend && pytest tests/smoke/test_pg_pool_and_flush.py -v
```

新增 `backend/tests/smoke/test_pg_pool_and_flush.py`（离线，不打真实 PG）：

1. **池上限**：`init_pool` 后 `pool.get_max_size() == 20`（用 conftest 的 `pg_pool` fixture，需 `DATABASE_URL`；无则 skip）。
2. **监控日志**：用 fake pool（暴露 `get_size/get_idle_size/get_max_size`）直调 `_log_pool_status`，`caplog` 断言：耗尽态（idle=0, size==max）打 `warning`，正常态打 `info`。
3. **监控生命周期**：`start_pool_monitor` 创建任务、`stop_pool_monitor` 取消后 `_pool_monitor_task is None`。
4. **flush 超时不重抛**：monkeypatch `_FLUSH_TIMEOUT=0.01` + patch `TraceRepository.save_trace` 为 `await asyncio.sleep(1)`，调 `flush()` 不抛、`_local` 已清理。
5. **flush 单点失败不重抛**（回归）：patch `save_trace` 抛注，`flush()` 不抛、`_local` 已清理。

回归：

```bash
cd backend && pytest -q
```

## Explicitly NOT doing

- **不做** 分析 SQL（`tools/sql_tools.py` 的 psycopg2）纳入 asyncpg 池——那是独立连接路径，改动面大、收益对立项诉求（asyncpg 池排队）不直接相关，留待 backend-async-refactor。
- **不做** 连接池动态伸缩（按需扩缩）——固定 `max_size=20` 已满足 ~15 并发目标，动态伸缩引入额外复杂度。
- **不改** `flush()` 的逐操作 try/except 语义——单点失败仍吞掉不影响整体，只在这之上加总超时。
- **不新增** observability 监控端点——本次只在进程内打日志，指标端点在既有 `observability.py` 范围外。