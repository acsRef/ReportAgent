# Plan: PostgresSaver 替代 MemorySaver（checkpoint 落 PG，跨重启持久）

> 状态: 已完成（依赖升级 langgraph-checkpoint 4.1.1 验证安全；共享单例 + 三图接线 + lifespan；跨实例持久化测试通过；全套 122 passed + 启动冒烟）

## Context（背景）

来源：CLAUDE.md 早已标注的生产改进；[2026-07-30-bug-review.md](2026-07-30-bug-review.md) B-3/B-8、[2026-07-30-cross-agent-state-safety.md](2026-07-30-cross-agent-state-safety.md) C-1 的彻底解法。

现状与问题：

- 三个图各自 `workflow.compile(checkpointer=MemorySaver())`：
  - `parent_graph.py:561`（legacy，模块全局单例 + 共享 MemorySaver）
  - `confirmed_execution_graph.py:519`（每请求新建）
  - `requirement_analysis_graph.py:183`（每请求新建）
- `MemorySaver` 是**进程内 dict**：
  1. 进程一重启，所有 in-flight checkpoint 丢失；
  2. 无法多 worker / 多实例部署（checkpoint 不共享）；
  3. legacy 跨请求状态只能靠全局共享 saver（带来 C-1 污染，目前用 per-session `asyncio.Lock` 兜底）。
- 代码里已预留正确接缝 `app/infra/checkpoint/factory.py::create_checkpointer(env)`，但 production 分支是 `NotImplementedError`，且**三个图都没接这个 factory**（直接写死 `MemorySaver()`）。

## Design（设计）

### 依赖变更（需确认）

- 新增 `langgraph-checkpoint-postgres`（提供 `AsyncPostgresSaver`）+ `psycopg[binary]`(v3) + `psycopg-pool`。
- **副作用**：`langgraph-checkpoint` 从 **4.0.3 升到 4.1.1**（postgres 版要求 `>=4.1.0,<5.0.0`）。`langgraph` 本体不动（1.1.10）。`psycopg2` 保留（`sql_tools` 仍在用）。
- 风险：核心依赖 minor 升级，需全套回归确认 `MemorySaver`/图行为不破。

### 单一 checkpointer（共享单例）

`AsyncPostgresSaver` 内部持连接池，**必须全进程共享一个**，不能每请求新建（否则每请求一个连接池）。

`app/infra/checkpoint/factory.py` 改造：

```python
_checkpointer = None  # 进程内单例

async def init_checkpointer() -> None:
    """启动期调用：按 APP_ENV 建立 checkpointer 并建表。"""
    global _checkpointer
    if app_env() == "development":
        _checkpointer = MemorySaver()          # 本地开发：内存，便于 notebook 单步
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    _checkpointer = AsyncPostgresSaver.from_conn_string(os.getenv("DATABASE_URL"))
    await _checkpointer.setup()                # 创建 langgraph checkpoint 表

def get_checkpointer():
    if _checkpointer is None:                  # 未走 lifespan 时的兜底（测试/脚本）
        return MemorySaver()
    return _checkpointer

async def close_checkpointer() -> None: ...    # 关闭 psycopg 连接池
```

`app_env()` 复用 `app.infra.auth.startup_guard.app_env`（fail-closed，未设置按 production）——**统一 APP_ENV 语义**，消除 factory 原有 `"dev"` 与全局 `"development"` 的不一致。

### 接线：三个图改用单例

- `build_parent_graph()` / `build_confirmed_execution_graph()` / `build_requirement_analysis_graph()` 把 `checkpointer=MemorySaver()` 改为 `checkpointer=get_checkpointer()`。
- `main.py` lifespan：`init_pool` 之后 `await init_checkpointer()`；`yield` 后 `await close_checkpointer()`。
- legacy 的 per-session `asyncio.Lock`（C-1）**保留**——PostgresSaver 解决持久化/多实例，锁继续串行化同 session 并发，二者正交。

### 语义变化

- checkpoint 落 PG 的 `langgraph` schema（`AsyncPostgresSaver.setup()` 自建表），按 `thread_id` 分桶、跨重启持久。
- 非开发环境重启不再丢 in-flight checkpoint；具备多实例部署前提。

## Files to change（文件改动）

- `backend/requirements.txt`：`+ langgraph-checkpoint-postgres`、`+ psycopg[binary]`（记录 langgraph-checkpoint 升 4.1.1）。
- `backend/app/infra/checkpoint/factory.py`：单例 + `init/get/close_checkpointer` + 统一 `app_env()`。
- `backend/app/agent/parent_graph.py` / `confirmed_execution_graph.py` / `requirement_analysis_graph.py`：`MemorySaver()` → `get_checkpointer()`。
- `backend/app/main.py`：lifespan 接入 `init_checkpointer()` / `close_checkpointer()`。
- `backend/tests/persistence/`：新增 checkpoint 持久化测试。

## Reused existing utilities（复用工具）

- `app.infra.auth.startup_guard.app_env()` —— 统一的 fail-closed 环境判定。
- `DATABASE_URL` —— 与现有 PG 同源。
- `AsyncPostgresSaver.setup()` —— 自建 checkpoint 表，无需手写 DDL。
- legacy per-session `asyncio.Lock`（C-1）保留，不重写。

## Verification（验证）

- **依赖回归（最关键）**：装完依赖后 `pytest -q` 全套绿，确认 `langgraph-checkpoint` 4.0.3→4.1.1 不破 `MemorySaver`/图测试。
- **持久化测试**（`tests/persistence/test_postgres_checkpoint.py`，persistence marker）：
  - 用一个最小编译图 + `get_checkpointer()`（强制 PostgresSaver）跑一轮，记录 `thread_id`；
  - **新建一个 checkpointer 实例**（模拟重启）按同 `thread_id` `get_state` → 能读回上一轮 state（证明跨实例持久）。
- **factory 单测**：`app_env()=="development"` → `MemorySaver`；非 dev → `AsyncPostgresSaver`（mock/跳过真连）。
- **启动冒烟**：非 dev 配置下 uvicorn 起，确认 checkpoint 表建成、`/health` ok。

## Explicitly NOT doing（明确不做）

- 不动 `sql_tools` 的 psycopg2（与 checkpoint 的 psycopg3 并存，async 迁移是另一条线）。
- 不移除 legacy per-session 锁（与 PostgresSaver 正交，继续防同 session 并发）。
- 不改图拓扑/节点逻辑，只换 checkpointer。
- 不做 checkpoint 的定期清理/TTL（langgraph 表会增长，清理策略独立排期）。
- 不在 dev 默认开启 PostgresSaver（保留 MemorySaver 便于本地单步调试）。
