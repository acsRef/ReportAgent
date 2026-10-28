> 状态: 进行中

# 执行幂等：同一份确认需求只执行一次

## Context

`_release_draft_lock` 在执行结束后会把 draft 从 `locked` 放回 `complete`（为了让用户能重新生成 / adjust / PATCH）。副作用是：**同一份已确认需求可以被反复执行**——

- 用户重复点「确认并生成报告」
- 客户端超时后重发（前端 60s 兜底定时器曾制造过这种重发）
- `POST /sessions/{sid}/retry` 委托到 confirm

竞态窗口（同一时刻两发）由 `ExecutionRegistry` 的 `BusyError → 409 SESSION_BUSY` 挡住；但**串行重复**没有任何保护：第二次会再跑一遍 SQL、再落一条内容相同的新版本。这正是 `docs/testing-boundaries.md` 里 B7 边界（"不重复执行"）的空洞。

## Design

**判定键 = `session_id:draft_id`。** draft 一旦被 PATCH 就换成新 id，所以"需求改过"不会被误判成重放。

新增图节点 `idempotency_guard`，接在 `load_confirmed_requirement` 之后、`sql_gate` 之前：

```
security_guard → load_confirmed_requirement → idempotency_guard ─┬─ replay  → END（重放，不执行不落库）
                                                                  └─ execute → sql_gate → data_agent → sql_agent → report_agent → persist_report
```

三条不变量（都有测试钉住）：

| 情形 | 行为 | 理由 |
|---|---|---|
| 已有 `status='done'` 版本（SUCCESS / EMPTY） | **重放**该版本，`END` | 已执行过；再跑就是重复执行 + 重复落库 |
| 只有 `status='error'`（FAILED） | **继续执行** | 失败必须可重试，否则一次失败把 draft 永久锁死 |
| `base_report_version` 非 None（mode=adjust） | **不参与幂等** | adjust 的语义就是产出新版本 |

重放的返回：`report_payload` merge 上 `version / parent_version / title`，`execution_status` 取原 payload 的 verdict。父图（`main.py`）据此发 `report` 事件——**前端零改动**，版本号不变，用户看到的是同一份报告。

判定口径收敛在仓储单点 `report_version_repository.find_done_by_draft()`（`status = 'done'` 且 `requirement_draft_id = $3`），SQL 层保证 FAILED 被排除。

审计：命中重放时 `tracer.add_decision(name="idempotency_guard", action="replay", idempotency_key, replayed_version)`，Langfuse / PG trace 都能查到。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/infra/db/report_version_repository.py` | 新增 `find_done_by_draft()`（只认 `status='done'`） |
| `backend/app/agent/confirmed_execution_graph.py` | 新增 `_idempotency_guard` 节点 + `_route_after_idempotency` + 图接线；state 加 `idempotency_key` / `replayed_version` |
| `backend/tests/graphs/test_idempotent_confirm.py`（新） | 5 例：重放 / 无历史执行 / adjust 旁路 / 路由 / 图级重放不执行 SQL |
| `backend/tests/persistence/test_idempotency_guard_pg.py`（新） | 真 PG：FAILED 可重试、done 重放、重放不新增行 |
| `backend/tests/graphs/test_confirmed_routing.py` | stub 掉幂等闸（该测试不碰 DB） |
| `backend/tests/api/test_confirm_background.py` | 同上 |
| `docs/testing-boundaries.md` | B7 从 ⚠️ 改为 ✅ |

## Reused existing utilities

- `agent.report_version.requirement_draft_id` 列**早就存在**（P-4 draft 一致性就靠它），无需新表 / 新迁移。
- `ExecutionRegistry` 继续负责**并发**互斥（409），本改动只补**串行**幂等——两者职责不重叠。
- `traced_node` + `current_tracer().add_decision` 复用现有审计面（P13 Langfuse 会自动收到）。
- `_route_after_gate` / `_route_after_diagnose` 的条件边写法直接照搬，不引入新范式。
- 前台无需改动：父图读 `report_payload` 的契约不变。

## Verification

1. **单测**：`pytest tests/graphs/test_idempotent_confirm.py -q` → 5 passed。
2. **真 PG**：`pytest tests/persistence/test_idempotency_guard_pg.py -q` → 1 passed（FAILED 不重放 / done 重放 / 行数不变）。
3. **全量回归**：`pytest -q` → **1188 passed / 1 skipped**（1182 baseline + 6 新增，零回退）。
4. **真链路**（脚本 `.e2elogs/verify_idempotency.py`，连 :8100 真后端）：
   ```
   confirm#1 events = [phase, trace×12, report(v1), done]
   confirm#2 events = [phase, report(v1), done]      ← 无 trace，说明没执行
   report_versions  = [1]                            ← 没有重复行
   ```
   → **重复执行率 = 0**。

## Explicitly NOT doing

- 不新增 `agent.execution_attempt` 表（`report_version.requirement_draft_id` 已能表达"这份 draft 执行过"）。
- 不做"重放时返回 409 让前端提示"——重放返回同一版本对用户更自然，且前端零改动。
- 不改 `ExecutionRegistry` 的 409 语义（并发场景仍应拒绝，而不是排队）。
- 不做跨进程幂等（多实例同时 confirm 仍靠 DB 锁 + 409；分布式幂等键留到真有部署需求时）。
- 不动 adjust 的版本链语义（每次都新版本是它的定义）。
