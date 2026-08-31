# P12 Review-Prep 修复（针对 review 6 重点审查项的 4 项加固）

> 状态: 已完成（2026-08-31；p12-playwright 分支 4 commit：`a55db1f` 截图归档 setup / `f2e415c` Fix 1 get_chat_llm fail-closed / `62b4ff9` Fix 4 mock fixture key 校验 / `0325cbe` Fix 3 spec 03 SUCCESS 证据 / `3722637` Fix 2 spec 07 落库断言 + LLM_MOCK_DELAY_MS 替代 pg_sleep）
> 上游: P12 已合 `p12-playwright` 分支 6 commit（580b42b / 0873be7 / 4ae1610 / 48222f2 / 3224a8d / eebf4cd）；review 反馈 6 项中 4 项已主动加固。

## Context

P12 实施完成后 user review 6 项重点审查反馈（**真实落地偏差源于已经代码审查**，不是事后补救——P12 主实施已合，但 review 暴露若干 fail-open / spec 弱项需主动收口）：

| # | 审查点 | 当前状态 | 本 plan 处理 |
|---|---|---|---|
| 1 | MockLLM keying v2 是否掩盖流程回归 | ✓ 接受（marker + seq + fail-fast miss） | — |
| 2 | `LLM_PROVIDER=mock` 的 fail-closed | **✗ `get_chat_llm` 旁路**（`app/llm/__init__.py:88` + `llm_legacy.py:80` 直接 `ChatOpenAI(**base)`，绕过 `get_llm_adapter` switch；目前无 caller，但 `__all__` 导出 = 潜在 fail-open） | **加固（Fix 1）** |
| 3 | Specs 是否真验证 contract | 9/10 强（happy/clarification/empty/failed/version/recovery/memory/trace OK）；**spec 03 retry 间接、spec 07 background-execution 弱** | **Fix 3 + Fix 2 加固** |
| 4 | trace-progress 50ms DOM 轮询 vs SSE transport | ✓ 分层（vitest 单测覆盖 SSE parser + model） | — |
| 5 | failed-result 是否真走 SQL validator | ✓✓ 强（fixture 真触 sqlglot validate + DiagnosePolicy repair budget） | — |
| 6 | background-execution 是否证明"后台跑完" | **⚠️ 只断言 toast 文案，没断言 session.phase=report_ready 证据** | **Fix 2 加固** |
| 4 项加固 | — | Fix 1 + Fix 2 + Fix 3 + Fix 4（本 plan） |
| — | spec 04 fixture sanity check | 可选 low-cost 防 fixture typo 静默兜底 | Fix 4 |

## Design

### Fix 1【高】`get_chat_llm` fail-closed（review #2）

**问题**：`get_chat_llm()` 是 dead export（grep 全代码库 0 caller），但 `app/llm/__init__.py:95 __all__` 仍导出它。任意新代码一行 import 就直接 `ChatOpenAI(**base)` 走真实 LLM，**Contract E2E 立刻半 mock**。

**改法**：
- `app/llm/__init__.py`：`get_chat_llm()` 在 `LLM_PROVIDER == "mock"` 时抛 `NotImplementedError("LLM_PROVIDER=mock 禁用直接 ChatOpenAI；用 get_llm_adapter().generate()")`；否则保留原行为（real path 仍可用）
- `app/llm/__init__.py`：`__all__` 移除 `"get_chat_llm"`（防新代码 import 它）
- `app/llm_legacy.py:80`：`get_chat_llm` 删掉或改为调用 `app.llm.get_chat_llm`（去重）；它已 deprecated
- **Tests**（`backend/tests/contracts/test_mock_llm_adapter.py`）：新增 2 例
  - `test_get_chat_llm_in_mock_mode_raises_not_implemented`：monkeypatch `LLM_PROVIDER=mock`，调 `from app.llm import get_chat_llm` 抛 `NotImplementedError`
  - `test_get_chat_llm_in_real_mode_constructs`：默认（LLM_PROVIDER unset）调 `get_chat_llm()` 返回 `ChatOpenAI` 实例（保证真实路径不被破坏）
- 风险：grep 全代码库 0 caller（已验证），删/raise 安全

### Fix 2【高】spec 07 background-execution 增强（review #6 + #3）

**问题**：当前 spec 07 只断言 `body` 含「报告已在后台生成，可查看」。**没断言**：session 真翻到 `report_ready` + report v1 真落库 + 后端 5s 轮询真触发。

**改法**（`frontend/e2e/specs/07-background-execution.spec.ts`）：
- 截获 `activeSessionId`（sendQuery 之前 capture page.evaluate `localStorage.getItem('ragent_auth')` 取不到的 session id——改为 sendQuery 后通过 `request.get('/api/v1/sessions/...')` 找最新 session，或更简单：在 startNewSession 后通过 page 暴露的 `__activeSessionId`/`window` 上 store 拿）
  - 简化方案：toast 后用 `page.request.get(`${BACKEND_URL}/api/v1/sessions?limit=5`)` 找最近 session（含 sid）
- `await stopGenerating()` 点击记录在 `Date.now()`，作为时间基准
- toast 出现后（`<=30s`）：assert `GET /sessions/{sid}` 返回 `session.phase === 'report_ready'` + `latest_report.execution_status === 'SUCCESS'`
- 这条 assert 证明：stop 后 backend 任务继续跑完 → 落库 report v1 → 前端轮询看到 report_ready → 触发 toast。三段因果锁链都钉住
- **Tests 改动**：spec 07 增强（已存在的 PASS 基础上加 1 个 session 状态断言）

### Fix 3【中】spec 03 retry 显式修复证据（review #3）

**问题**：当前 spec 03 只断言最终 report 含「华东」。**没显式断言**：report 的 `execution_status === 'SUCCESS'`（证明 repair 后 sql_generate:2 真成功落库 SUCCESS，未掉到 FAILED）。

**改法**（`frontend/e2e/specs/03-retry.spec.ts`）：
- confirm 后 `page.request.get(`${BACKEND_URL}/api/v1/sessions/${sid}/reports/1`)` 
- assert `response.execution_status === 'SUCCESS'`（不是 FAILED）
- 配合现有 `report contains 华东`，**两端**锁住「修复路径收敛到 SUCCESS」
- 同时可选：在 confirm SSE body 里 grep `"修复"` 或 `"诊断"` 节点名（spec 10 已用 DOM 轮询覆盖 trace；这里再在 SSE 层打一条轻量补丁不必要——pass）

### Fix 4【低】mock fixture load sanity check（review 整体兜底）

**问题**：fixture 文件手写，若 key typo（如 `sql_plan1` 漏冒号 / `SQL_Plan:1` 大小写错）→ `prompt_kind` 不匹配 → **miss 时 fixture 完全没用**，后端真 LLM 也不工作（mock 模式无 key → Miss 抛错）—— 这种 typo 反而会让 mock miss 暴露，但不直接。**真正静默兜底是 fixture key 含特殊字符被 Python 解析正常但语义模糊**。

**改法**（`backend/app/llm/mock.py` `_load_case`）：
- 加 `import re` + `_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*:[1-9][0-9]*$")`
- 每条 key 必须 match `_KEY_RE`；不匹配抛 `MockLLMMiss(f"fixture key '{k}' 不符合 kind:N 格式（kind 需小写蛇形，N ≥ 1）")`
- **Tests**（`backend/tests/contracts/test_mock_llm_adapter.py`）：新增 1 例
  - `test_load_case_rejects_malformed_key`：写 `{ "sqlplan1": {...} }` 期望 `MockLLMMiss`
- 风险：现有 8 个 fixture 文件的 key 全部匹配（小写+冒号+数字），无需改 fixture

### Files to change（按 Fix 编号）

| Fix | 模式 | 路径 |
|---|---|---|
| 1 | 修改 | `backend/app/llm/__init__.py`（get_chat_llm mock raise + `__all__` 移除） |
| 1 | 修改 | `backend/app/llm_legacy.py`（get_chat_llm 去重/删除） |
| 1 | 修改 | `backend/tests/contracts/test_mock_llm_adapter.py`（新增 2 例） |
| 2 | 修改 | `frontend/e2e/specs/07-background-execution.spec.ts`（增加 session phase/execution_status 断言） |
| 3 | 修改 | `frontend/e2e/specs/03-retry.spec.ts`（增加 `execution_status === 'SUCCESS'` 断言） |
| 4 | 修改 | `backend/app/llm/mock.py`（`_load_case` 加 key 格式校验 + logger） |
| 4 | 修改 | `backend/tests/contracts/test_mock_llm_adapter.py`（新增 1 例） |
| Docs | 修改 | `docs/plans/2026-08-30-p12-playwright.md`（P12 落地记录补 4 项加固 commit ref） |
| Docs | 修改 | `docs/plans/README.md`（索引登记新 plan） |

### Reused existing utilities

- `app/llm/mock.py _load_case` — 现有 fixture 加载入口（Fix 4 在此处扩）
- `app/llm/__init__.py get_llm_adapter()` — 单例 + env switch 已落（Fix 1 不动这里）
- `app/llm/mock.py MockLLMMiss` — 已有异常，Fix 1/Fix 4 复用同一异常类型
- `frontend/e2e/helpers/auth.ts` — `page.request.post(BACKEND_URL/...)` 模式（Fix 2/3 GET 复用）
- `frontend/e2e/helpers/env.ts` — `BACKEND_URL` 常量（Fix 2/3 复用）
- `frontend/src/components/workbench/ReportPaper.tsx` — `fetchReportVersion` 返回 `execution_status`（Fix 3 读此字段）

### Verification

```bash
# Backend（Fix 1 + Fix 4 + 新测试）
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts -q
# 预期：原 513 passed + 新增 3 例 = 516 passed

cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：≥ 941 passed 红线（现 948 baseline）仍满足，新增测试全绿

# Playwright Contract（Fix 2 + Fix 3）
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/03-retry.spec.ts e2e/specs/07-background-execution.spec.ts
# 预期：2 spec 全绿（spec 03 含新 SUCCESS 断言；spec 07 含新 session phase 断言）

# 完整 Contract 套件无回归
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/0[0-9]-*.spec.ts e2e/specs/10-*.spec.ts
# 预期：11 全绿
```

冒烟矩阵：
- [ ] Fix 1：`LLM_PROVIDER=mock` 调 `get_chat_llm()` 抛 `NotImplementedError`
- [ ] Fix 1：默认（unset）调 `get_chat_llm()` 仍返回 ChatOpenAI
- [ ] Fix 2：spec 07 在 toast 出现后 GET session 返回 phase=report_ready + execution_status=SUCCESS
- [ ] Fix 3：spec 03 在 confirm 后 GET report v1 返回 execution_status=SUCCESS
- [ ] Fix 4：fixture key `sqlplan1`（缺冒号）load 抛 `MockLLMMiss`
- [ ] Fix 4：现有 8 个 fixture 文件 key 全 match（无 fixture 文件需改）
- [ ] 全量 backend 948+3=951 passed（≥ 941 红线）
- [ ] Playwright 11 Contract specs + 1 Full 全绿无回归

### Explicitly NOT doing

- **不在 mock 层加 prompt 内容语义校验**（review #1 提议）—— fixture 是手写响应，prompt 内部 schema/dictionary 漂移的覆盖是 contract test 的事，不属 P12 follow-up
- **不改 `LLMAdapter.generate` 接口**——已有契约稳定
- **不重构 `get_chat_llm` 为 mock-aware 函数**——直接 raise（dead code 没必要扩展）
- **不修 trace-progress 改 SSE body 抓取**——已确定 50ms DOM 轮询 + vitest transport 单测 分层正确
- **不修 failed-result 的 report error band 渲染选择**（review 误把 ErrorCard 当目标）——设计本就是 ReportPaper 历史 FAILED 版本视图，已在 spec 05 验证
- **不动 P12 已落地的 6 个 commit**——4 项加固作为**独立新 commit**（不 amend、不 rebase），commit message 前缀 `fix(p12): review prep — ...`
- **不推到远端**（user 走 GitHub connector review 后再决定）

### Commit 计划（4 个独立 commit，按依赖排序）

```
fix(p12): review prep — get_chat_llm fail-closed (mock 模式禁用 ChatOpenAI 直构造)
  → backend/app/llm/{__init__,legacy}.py + test

fix(p12): review prep — mock fixture key 格式校验（防 typo 静默 miss）
  → backend/app/llm/mock.py + test

fix(p12): review prep — spec 03 retry 加 execution_status=SUCCESS 证据
  → frontend/e2e/specs/03-retry.spec.ts

fix(p12): review prep — spec 07 background-execution 加 session 落库断言
  → frontend/e2e/specs/07-background-execution.spec.ts

docs(p12): review prep 落地记录 + plan 收尾
  → docs/plans/2026-08-30-p12-playwright.md + 本 plan README 登记
```

### 落地记录

| Fix | Commit | 命中验证 |
|---|---|---|
| 截图归档 setup（outputDir + README + gitignore） | `a55db1f` | `npm run e2e:contract` 失败用例产物落到 `frontend/e2e/artifacts/<project>/<spec>/test-failed-N.png` + trace.zip |
| Fix 1 `get_chat_llm` fail-closed | `f2e415c` | `test_get_chat_llm_in_mock_mode_raises_not_implemented` + `test_get_chat_llm_in_real_mode_still_constructs`（513→515 contracts passed） |
| Fix 4 mock fixture key 格式校验 | `62b4ff9` | `_FIXTURE_KEY_RE = ^[a-z][a-z0-9_]*:[1-9][0-9]*$`；`test_load_case_rejects_malformed_key` 钉住 3 类 typo（缺冒号 / 大写 / seq=0）（515→516 contracts passed） |
| Fix 3 spec 03 retry SUCCESS 证据 | `0325cbe` | `page.evaluate` 走浏览器 context 拿 token，GET `/sessions/{sid}/reports/1` 断言 `report.execution_status === 'SUCCESS'`（锁住 repair 真收敛） |
| Fix 2 spec 07 background-execution 落库断言 | `3722637` | 同上模式：toast 出现后断言 `session.phase === 'report_ready'` + `report.execution_status === 'SUCCESS'`；同时**修正执行机制**：移除 `pg_sleep(3)`（被 `validate_sql` 显式拒绝），改用 `LLM_MOCK_DELAY_MS=3000` env 拉长 mock 生成窗口 |

**总体验证**：backend **951 passed / 1 skipped / 5 warnings**（baseline 948 +3 新增；≥ 941 红线 ✓）；Playwright **11/11 Contract specs + 1 Full spec 全绿**。

### 偏差（落地中暴露 + 立即修复）

1. **Fix 2 第一次落地失败**：`pg_sleep(3)` 被 `validate_sql` 显式拒绝（"禁止调用危险函数 pg_sleep，仅支持对业务表的只读查询"），spec 07 走错路径到 phase=error。**解决**：在 `MockLLMAdapter` 加 `delay_ms` 字段（env `LLM_MOCK_DELAY_MS`）+ `_lookup` 里 sleep，background-execution fixture 的 SQL 改回合法业务表 JOIN。**教训**：Contract spec 通过 mock 拉长 LLM 调用窗口，不能依赖 SQL 自身的"慢"——validate_sql 是更严格的安全门。

### 风险评估

| 风险 | 等级 | 缓解 |
|---|---|---|
| Fix 1 删 `get_chat_llm` 误伤 caller | 低 | grep 0 caller 已验证；保留函数 + raise 兜底 |
| Fix 2 spec 07 新断言 session-id 取不到 | 中 | 通过 `GET /sessions?limit=5` 找最新一条（不依赖 zustand 内部 store id） |
| Fix 3 spec 03 新断言 timing | 低 | confirm 流结束 → report 落库 → fetch 立刻返回 SUCCESS，已跑通 happy 路径验证 |
| Fix 4 严格 key 校验影响未来 fixture | 极低 | 校验规则明确（kind 小写+冒号+数字），文档化 |
| 4 个独立 commit 与 P12 主落地 6 commit 关系 | 极低 | fix 提交不 rebase/amend，commit 历史清晰 |