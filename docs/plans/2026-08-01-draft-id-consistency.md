# Plan: confirmed-execution 锁定与执行的 draft 一致性（P-4）

> 状态: 已完成（P-4 落地；4 测试，全套 119 passed）

## Context（背景）

来源：[2026-07-30-bug-review.md](2026-07-30-bug-review.md) P-4（LOW）。

`confirmed_execution_graph.py` 的 `_draft_id_from_state` 在**执行中途**重新查询「当前 session/user 的最新 draft id」。流程是：`_load_confirmed_requirement`（加载卡）→ `_sql_gate`（锁定 draft）→ … → `_persist_report`（落库）。gate 与 persist 都通过 `_draft_id_from_state` **各自重查一次**最新 draft。

问题：若在 load 之后、gate/persist 之前，用户恰好 PATCH 了一次需求（新增了一个更高 version 的 draft），则：

- `_sql_gate` 的 `lock_for_execution` 锁住的是**新** draft；
- 但 state 里 carry 的 `RequirementCard` 还是 load 时的**旧**卡；
- 结果：锁定的卡 ≠ 实际生成 SQL 用的卡。语义错位。

窗口很短（子图执行几秒内）、触发概率极低，且不会崩溃（lock 的是真实存在的新 draft），但属于「接口是契约」被破坏——state 携带的卡与锁定的 id 本应指向同一份 draft。

## Design（设计）

**单一来源**：draft_id 在 load 阶段确定一次，写入 state，全链路只读它，不再中途重查。

1. `ConfirmedExecutionState` 增加 `draft_id: Optional[int]` 字段。
2. `_load_confirmed_requirement` 的**两个返回分支**（locked 幂等重载 / 正常 complete）都带上 `draft["id"]`（`get_latest` 的返回行本就含 `id`）。
3. `_draft_id_from_state` 从「开连接重查最新 draft」改为「只读 `state.get("draft_id")`」，缺失时回退 `0`——`0` 会让 `lock_for_execution` 走既有的「draft 不存在」失败路径，优雅报错而非误锁。

改后该函数名副其实：draft id 来自 state，不来自二次查询。load 与 gate/persist 之间即使发生 PATCH，执行的仍是 load 时确定的那份 draft，锁定与执行始终一致。

## Files to change（文件改动）

- `backend/app/agent/confirmed_execution_graph.py`
  - `ConfirmedExecutionState` 加 `draft_id: Optional[int]`
  - `_load_confirmed_requirement` 两个返回分支补 `"draft_id": draft["id"]`
  - `_draft_id_from_state` 改为 `async def ... return state.get("draft_id") or 0`（删除内部 `get_pool` 重查）
- `backend/tests/graphs/`：新增对 `_draft_id_from_state` 与 `_load_confirmed_requirement` 返回 draft_id 的单测

## Reused existing utilities（复用工具）

- `app.infra.db.requirement_repository.get_latest` —— 已 SELECT `id`，无需新增查询。
- `requirement_service.lock_for_execution` 既有的「draft 不存在」失败路径 —— 回退 `0` 直接复用它，不新增错误分支。

## Verification（验证）

- 新增单测：
  - `test_draft_id_from_state_reads_state`：state 带 `draft_id=7` → 返回 `7`（不再访问 DB）。
  - `test_draft_id_from_state_falls_back_to_zero`：state 无 `draft_id` → 返回 `0`。
  - `test_load_confirmed_requirement_sets_draft_id`：mock `get_latest` 返回 `{"id": 3, "status": "complete", ...}` → 返回 dict 含 `draft_id == 3`。
- 回归：`pytest -m graphs -q` 全绿。

## Explicitly NOT doing（明确不做）

- 不改 `lock_for_execution` / `lock_draft` 的锁逻辑。
- 不改 `get_latest` 的查询。
- 不引入新表/新列（draft_id 已在 `requirement_draft.id`）。
- 不处理「load 后 PATCH 是否应中断执行」这一产品语义——本 plan 只保证「锁定 == 执行」的一致性。
