# 会话硬删除：DELETE 端点 + 8 张表级联 + CLI 批量清理

> 状态: 已完成（2026-11-11）

## Context

用户问：「对话不能删的吗？」

取证结论：**确实不能删，功能缺失而不是 bug。**

- `backend/app/main.py` 里 8 个 session 端点（列表 / 快照 / PATCH 需求卡 / confirm / retry / favorite / 读报告 / 导出），**没有一个 `DELETE`**；全文件 `@app.delete` 命中数为 0。
- `agent.session` 表没有 `deleted_at` / `archived` 之类的列（`information_schema` 查过）。
- 前端 `src/` 里 `deleteSession|removeSession|删除对话|删除会话|onDelete` 全部 0 命中——连入口都没有。

同时暴露出第二个问题：库里 **215 条会话，其中 150 条是 `eval-*`**（评测基线跑出来的副产物）。它们和真实对话混在同一个列表里，这才是侧边栏难看的主因。所以只加按钮不够——已有的堆量也要有工具清掉。

用户已明确选择**硬删**（真删库、不可恢复），不做软删/回收站。

## Design

### D1 级联必须按外键顺序，且单事务

表与依赖（`information_schema` 实查）：

| 步骤 | 表 | 键 | 说明 |
|---|---|---|---|
| 0 | `agent.session` | `thread_id` | 先把 `latest_requirement_draft_id` 置 NULL，否则第 6 步报 `fk_session_latest_requirement` |
| 1 | `observability.agent_trace_span` | 经 `trace_id` 子查询 | 子表先删（无直接 session_id 列） |
| 2 | `observability.agent_trace` | `session_id` | |
| 3 | `public.checkpoint_writes` | `thread_id` | **隐藏坑**：不删则同一 thread_id 重建会读到旧图状态 |
| 4 | `public.checkpoint_blobs` | `thread_id` | 同上 |
| 5 | `public.checkpoints` | `thread_id` | 同上 |
| 6 | `agent.analysis_task_revision` | `session_id` | |
| 7 | `agent.report_version` | `session_id` | **必须先于 draft**：`report_version_requirement_draft_id_fkey` |
| 8 | `agent.requirement_draft` | `session_id` | |
| 9 | `memory.semantic_entry` | `session_id` | 会话级记忆 |
| 10 | `memory.query_failure` | `session_id` | |
| 11 | `app.conversations` | `session_id` | 对话正文 |
| 12 | `agent.session` | `thread_id` | 最后 |

全链条在**一个事务**里：中途失败整体回滚，不出现「对话没了但报告还在」的半删状态。

`memory.long_term_memory` 没有 session 维度（列已核），不参与；`memory.query_template` 按 user 维度，也不动。

### D2 端点语义

`DELETE /api/v1/sessions/{session_id}`

| 情况 | 响应 |
|---|---|
| 会话不存在 / 属于他人 | **404 `SESSION_NOT_FOUND`**（与 PATCH / confirm / retry 一致，不泄露存在性） |
| 该 session 有未完成的后台任务 | **409 `SESSION_BUSY`**（复用 `registry.get_confirmed_task` 的 `finished` 判定；正在跑的任务会持续写 `report_version`，此时删会写出孤儿行） |
| 正常删除 | 200，返回 `{"deleted": {表名: 行数}}`，行数供前端 toast 与运维核对 |

### D3 前端入口

- `SessionRail` 每个会话项 hover/active 时出现「删除」按钮（不占默认视觉）；
- 点击弹 `atelier/Modal` 二次确认（项目既有组件，不用 `window.confirm`），文案写明「不可恢复」；
- 删除成功：dispatch `sessions/removed`（从列表摘掉、offset 减一）；
- **删的是当前会话** → 同时走 `handleNewAnalysis()`（断流 + 清过程 + 回新分析），否则界面会停在一个已不存在的 session 上；
- 失败（409 忙 / 其他）→ toast 显示原因，不做本地摘除（不回滚假成功）。

### D4 批量清理走 CLI，与端点共用同一份级联

`python -m app.jobs.purge_sessions --prefix eval- --dry-run`

- 复用 `app/services/session_deletion.py` 的同一个函数（**只有一套级联逻辑**，不在 CLI 里重写 SQL）；
- `--dry-run` 只列将删的 session（id + 标题 + 版本数），默认不删；
- 真删需要显式去掉 `--dry-run`，并打印每条的删除行数汇总。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/services/session_deletion.py` | 新增：有序级联清单 + `delete_session()`（单事务、owner 校验、返回各表行数） |
| `backend/app/main.py` | 新增 `DELETE /api/v1/sessions/{session_id}`（404 / 409 / 200 三态） |
| `backend/app/jobs/purge_sessions.py` | 新增 CLI（`--prefix` / `--user` / `--dry-run`） |
| `backend/tests/persistence/test_session_delete.py` | 新增：真 PG 下各表建行 → 删 → 全表归零；他人会话 → None |
| `backend/tests/contracts/test_session_delete_gate.py` | 新增：忙闲门与 owner 分支（fake 池，不依赖 PG） |
| `frontend/src/api/sessionsClient.ts` | 新增 `deleteSession()` |
| `frontend/src/stores/analysisReducer.ts` | 新增 `sessions/removed` |
| `frontend/src/components/workbench/SessionRail.tsx` | 删除按钮 + 确认 Modal |
| `frontend/src/pages/WorkbenchPage.tsx` | `handleDeleteSession` 接线（删当前会话时回新分析） |
| `frontend/src/styles/workbench.css` | `.wb-session-del` 样式（hover 才显形） |
| `frontend/src/stores/__tests__/analysisReducer.test.ts` | `sessions/removed` 用例 |
| `frontend/src/components/workbench/__tests__/SessionRail.test.tsx` | 删除流程用例 |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `app/infra/db/postgres.get_pool()`：共享 asyncpg 池，`async with conn.transaction()` 保证单事务。
- `app/infra/execution/registry.get_confirmed_task()`：判断 session 是否还在跑（不新增状态）。
- `app/main.py` 既有的 `_require_session_owner` / `session_manager.get_session()` 校验范式（404 语义一致）。
- `frontend/src/components/atelier/Modal`：二次确认弹层。
- `frontend/src/stores/analysisReducer.ts` 既有 `sessions/*` 动作族与 `analysis/reset`。
- `WorkbenchPage.handleNewAnalysis()`：删除当前会话后的状态复位（断流 + 清过程）。

## Verification

1. **定向 pytest**（真 PG 已在本机可用）：
   ```
   pytest tests/persistence/test_session_delete.py tests/contracts/test_session_delete_gate.py
   ```
2. **真实请求**：建会话 → 出报告 → `DELETE` → 再 `GET` 得 404 → 列表里消失 → 库里 12 张表计数为 0。
3. **忙时保护**：confirm 发起后立刻 `DELETE` → 期望 409。
4. **CLI**：`--dry-run` 打印 150 条 `eval-*`；真跑后 `select count(*) from agent.session` 只剩真实会话。
5. **前端**：`npx vitest --run src/stores/__tests__/analysisReducer.test.ts src/components/workbench/__tests__/SessionRail.test.tsx` + `npx tsc -b`。

### 实测结果（2026-11-11）

| 检查 | 结果 |
|---|---|
| 定向 pytest（新增 7 条） | 7 passed |
| 全量 backend | **1827 passed / 18 skipped**（基线 1820 → +7） |
| 全量 frontend | **498 passed（60 文件）**，`tsc -b` 0 error |
| 真链路 DELETE | 200，返回各表行数：`agent_trace_span 67` / `app.conversations 5` / `report_version 1` / `requirement_draft 1` / `agent.session 1` |
| 删后 `GET /sessions/{sid}` | **404**；重复 DELETE 也 404 |
| 删后库内计数 | 12 张表全 0 |
| 任务在跑时删 | **409** `SESSION_BUSY: 任务仍在执行，稍后再删` |
| 浏览器全流程（Playwright） | hover → ✕ → 弹层（含「不可恢复」）→ 删除 → 行消失 + toast「已删除该会话」，0 console error |
| CLI 批量清理 | `--dry-run` 列 150 条；`--yes` 真删 150 条：**7891 个 span / 795 条对话 / 100 个报告版本 / 243 张需求卡**；孤儿检查（`report_version`、`conversations` 指向已删会话）均为 **0** |

补充说明两点：

- 持久化测试一开始只删不兜底，中途断言失败会把半截会话留在库里（实际漏过 2 条 `test-del-*`）。已改成 `try/finally` 里 best-effort 收尾，重跑后会话总数不变。
- 浏览器验证用的是真实 dev server（3000）+ 真后端（8100），不是 mock。


## Explicitly NOT doing

- **不做软删 / 回收站**：用户明确选硬删，不引入 `deleted_at` 与「已删除」视图。
- **不做批量勾选删除**：前端只做单条删除 + CLI 批量。
- **不做管理员跨用户删除**：端点只允许删自己的会话。
- **不改评测写入策略**：`eval-*` 之所以进列表是评测直接复用 workbench 会话模型，本次只提供清理工具，是否让评测用独立账号另议。
- **不删 `memory.query_template` / `long_term_memory`**：前者按 user、后者无 session 维度。
