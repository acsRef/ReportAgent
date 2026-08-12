# 2026-08-12-draft-lock-release

> 状态: 已完成

## Context

浏览器验证（execution-background-run 落地时）发现：confirm 成功后 draft 永久 `locked`，导致三类操作被拒：

- **同一 draft 重复 confirm**（用户想重新生成同一个需求）→ `REQUIREMENT_INCOMPLETE: failed to lock draft for execution: ... is in status 'locked', must be 'complete' to lock`
- **adjust**（对已生成报告说「再按产品细分」，mode=adjust）→ 同上
- **PATCH 修改需求** → `RequirementLockedError`（`patch_requirement` 拒绝 locked draft）

`lock_for_execution`（backend/app/services/requirement_service.py）只在「draft 已 locked 且 session **无** report_version 行」时重置为 complete（为中途失败可重试设计）；一旦执行成功落库（有版本行），draft 永久锁定。这是**现状既有行为**，但在「后台跑完」语义（用户断连后重新生成/调整是常态）下成为真实 UX 缺口——尤其 adjust 是工作台核心路径。

## Design

**执行结束 = 释放锁**：`_persist_report` 节点（confirmed_execution_graph.py 的终端节点，SUCCESS/EMPTY/FAILED 三态都汇聚于此）在落库后把 draft 从 `locked` 释放回 `complete`。

- 释放后：重新 confirm → load 到 complete draft → 重新锁定 → 执行（满足「重新生成」）；adjust → 同样可执行；PATCH → 可修改需求。
- **并发保护不变**：同进程双跑由 ExecutionRegistry 的 409 SESSION_BUSY 挡；跨进程由 `lock_for_execution` 的 `UPDATE ... WHERE status='complete'` 原语挡（两个并发 confirm 只有一个能锁上）。
- **中途失败场景保留**：执行未走到 `_persist_report`（graph 中途异常）→ draft 保持 locked + 无版本行 → 下次 confirm 走 `lock_for_execution` 现有恢复逻辑（重置 complete → 重锁）→ 与现状一致。
- repository 补 `release_lock`（`locked → complete`，按 draft_id + user_id 过滤，幂等：非 locked 时 0 行不报错），DB 访问保持集中在 repository 层。

## Files to change

- `backend/app/infra/db/requirement_repository.py`：新增 `release_lock(conn, *, draft_id, user_id)`——`UPDATE ... SET status='complete', confirmed_at=NULL, updated_at=NOW() WHERE id=$1 AND user_id=$2 AND status='locked'`（幂等）。
- `backend/app/agent/confirmed_execution_graph.py`：`_persist_report` 成功落库后调用 `release_lock`（复用 `_draft_id_from_state` 取 draft_id）。
- 测试：`backend/tests/graphs/test_draft_lock_release.py`（新，`-m graphs`）——用既有 FakePool pattern（参考 tests/graphs/test_confirmed_draft_id.py）验证：落库后发 release_lock SQL、WHERE 含 draft_id+user_id+locked、早退分支（payload None）不释放。

## Reused existing utilities

- `_draft_id_from_state`（backend/app/agent/confirmed_execution_graph.py）——取 draft_id，原样复用。
- `requirement_repository.lock_draft`（同 repository）——新增的 `release_lock` 与其对称。
- `get_pool`（backend/app/infra/db/postgres.py）——repository 函数由调用方传入 conn（与现有 repository 签名一致）。
- 测试 FakePool/FakeAcquire pattern（tests/graphs/test_confirmed_draft_id.py）。

## Verification

```bash
cd backend && pytest tests/graphs/test_draft_lock_release.py -v
cd backend && pytest tests/graphs tests/api -q   # confirmed 相关回归
cd backend && pytest -q                          # 全量离线回归
```

真实链路（PG + 后端 :8100 + LLM key，复用 execution-background-run 的验证脚本思路）：
1. new → PATCH complete → confirm 跑完 v1 → **再次 confirm** → 应成功产生 v2（修复前：REQUIREMENT_INCOMPLETE）
2. v2 完成后发 chat mode=adjust「再按产品细分」→ 应成功产生 v3（修复前：lock 失败）
3. 执行期间（任务进行中）confirm → 仍 409 SESSION_BUSY（并发保护不破坏）

## Explicitly NOT doing

- 不改 `lock_for_execution` 的恢复逻辑（中途失败场景保持现状）。
- 不改 draft 版本模型（adjust 继续复用同一 draft，不新建）。
- 不做跨实例释放锁的协调（release 幂等；并发双跑由 lock 原语挡）。
- 不碰 ExecutionRegistry 409 语义（本 plan 只解锁，不放松并发保护）。

## 落地记录

**实现**：
- `requirement_repository.release_lock(conn, *, draft_id, user_id)`：`UPDATE ... SET status='complete', confirmed_at=NULL WHERE id=$1 AND user_id=$2 AND status='locked'`（幂等，非 locked 0 行）。
- `confirmed_execution_graph._persist_report`：三态落库后 `await _release_draft_lock(state)`（`_draft_id_from_state` 取 draft_id）；早退分支（payload None，无版本行）不释放，由 `lock_for_execution` 恢复逻辑兜底。

**测试**：新增 `tests/graphs/test_draft_lock_release.py` 4 测试（release SQL/跳过无 draft_id/persist 后调用/早退不调用）；`tests/test_sql_error_envelope.py` 三个 `_persist_report` 路由测试补 `_release_draft_lock` mock。全量 380 passed（仅剩 2 个预存在 FAQ 失败）。

**真实链路验证**（PG + :8100 + 真实 LLM）：confirm v1 SUCCESS → **re-confirm → v2 SUCCESS**（修复前 REQUIREMENT_INCOMPLETE）→ **adjust「再按产品细分」→ v3 SUCCESS**（修复前 lock 失败）→ 任务进行中 confirm → **409 SESSION_BUSY**（并发保护不破坏）。