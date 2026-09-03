# P15 reliability 收口（e2e 从「功能场景丰富」→「生产级验证体系」）

> 状态: 进行中
> 源：用户对 `p15-e2e-live` review 后的收口指令（2026-09-03）。**不再扩业务 case**；把
> reliability 从「单层有、跨层缺」收敛成 4 层契约：MCP Client ✅ → Tool/Boundary → Runtime
> Reliability → Real E2E（mcp-down 传播证明 / mcp-timeout e2e / background-timeout e2e /
> sse-disconnect ✅ / recovery ✅）。

## Context（背景）

`p15-e2e-live`（18 commit，HEAD `6dbfd8a`）已验证：正式 6 例 + 边界 7 例 + MCP-down 2 例
全真调用。用户 review 后给出 **9 项收口**（优先级见下表），核心诉求：把「flaky 当绿灯」、
「seam 激活无法证明」、「MCP 三码业务层可能被同拍平成 []」、「timeout 无统一
classification→retry→terminal 契约」这些可靠性缺口补上，**不再堆业务 case**。

## Design（设计）

执行顺序 = 用户指定 ①→⑨。逐项：

### ① E2E gate 统一（P1，纯测试）

两文件 pytestmark 的 `not os.getenv("REPORTAGENT_E2E")` → `os.getenv("REPORTAGENT_E2E") != "1"`。
现状 `REPORTAGENT_E2E=0` 时 `"0"` 为 truthy → 不 skip → 误跑。改后 `=1` 才跑、`=0` skip。
`evaluation/tests/test_real_rag_mcp_e2e.py` + `test_real_rag_mcp_edge_cases.py` 两处。
（backend seam `_enabled()` 已是 `== "1"`，一致。）

### ② mcp_down ContextVar → create_task 继承契约（P1，offline）

`backend/tests/contracts/test_mcp_down_seam.py` 补 async 契约：parent `scoped(True)` 时
`asyncio.create_task` 派生 child → child `mcp_down.active() is True`（contextvar 在 create_task
拷贝，confirm/adjust 后台任务继承正依赖此机制）；另补反向（scoped 外 create → child False）。

### ③ MCP-down live case 证明 seam 真激活（P1，test + live 收敛）

现状缺陷：`mcp_down_execution_degrade_and_recover` 允许 SUCCESS → seam 没传播也可能 PASS
（memory 兜住 schema 照样真 SUCCESS，已实测 A1）。改法：**obscure-table 确定性对比**——
执行 MCP-down 腿改用「memory/FAQ 从未含 SQL 的真实表组合」（dim_promotion/dim_customer 等，
KB 有、历次 e2e 从未执行过 → 无 schema 时 LLM 无法凭记忆写对精确列名）：
- leg1 confirm + `X-E2E-McpDown: on` → schema refresh 空 → LLM 只能瞎猜列名 → 真实
  object_not_found → repair 需 MCP（down 取不到 DDL）→ budget 耗尽 → **确定性 NOT SUCCESS**
  （若 seam 未传播，schema 在 → 同卡应 SUCCESS → 断言红，故非空）。
- leg2 同 session 再 confirm 无 header（MCP 恢复）→ schema 真取到 → SUCCESS（回滚恢复）。
- 硬断言 leg1 `NOT(SUCCESS)` ∧ 显式 error/FAILED；leg2 `SUCCESS` + 真行。
- 收敛：obscure 组合 live 2× 稳定后定案；若仍 LLM 漂移，回退方案 = 后端日志 marker 断言
  （`REPORTAGENT_BACKEND_LOG` 指向启动日志，grep `E2E seam: schema MCP unavailable`）。

### ④ `_patch_fill_all` 吞错误（P2，test util）

两文件共用 `_patch_fill_all`（定义在 e2e 文件，edge import）。现状 `if pr.status_code==200 …
return card` 吞 PATCH 失败 → 后移到 confirm 才报错、难定位。改 `pr.raise_for_status()` +
`return pr.json()["requirement"]`。

### ⑤ double_fact flaky（P1，稳定性）

连续跑完整 edge suite 3~5 次观察；若仍偶发：
- 首选修 **input**（同 query 双事实对比语义歧义是根因——缺「以哪张为基准」信息，requirement
  偶尔判 missing → fill 猜默认 → confirm SQL 质量彩票）。给 query 补明确基准与粒度，消除
  metric 歧义。
- 备选收紧断言到 honest terminal（**不优先**——SUCCESS+双事实同现 SQL 是本 case 的价值）。
- 不允许把 flaky 当绿灯；若 query 语义本身不可稳定驾驭则如实标注并降级。

### ⑥ MCP 三码业务层 matrix（P2，offline；现状已多钉子）

现状核实（read-only survey 已做）：`classify_mcp_error` 已把 MCP_TIMEOUT(recoverable
timeout) / MCP_UNAVAILABLE(connection, 非 recoverable) / MCP_INVALID_RESPONSE(other, 非
recoverable) 三码区分；`_retrieve_dict` dispatcher 已把 INVALID re-raise（绝不 HTTP
fallback，`test_rag_schema.py:379+` 钉）与 UNAVAILABLE/TIMEOUT fallback-if-allowed 区分；
`_validate_matches_contract` 已把空 matches 当合法 no-match（≠ INVALID，`mcp_client.py:503`
注释钉）。**业务层缺口**：schema 检索经 `search_tables_from_rag` 时三码仍被拍平成 []（设计
如此，工具契约对 Agent 不变、分类写 log）。本项交付 = **一份 consolidated 业务层 matrix
契约**钉「三码不互相同形 + no-match 独立 + 各自 classification/recoverable/failure_policy」，
把散落钉子收成一张表；**不**改工具契约扁平（破坏「SQL 生成不因 schema 检索阻塞」），只在
matrix 中显式声明该扁平是故意降级（由 ③ 的 obscure 腿证明其「不静默 SUCCESS」性质）。

### ⑦ timeout 统一 contract：`test_timeout_policy.py`（P1，offline）

逐 timeout 钉「什么超时 / 谁 retry / 谁 fail / 用户看到什么」：
- **LLM request**：`retry.py` LLM_TIMEOUT(90s 预算/2 retry/退避) → classify → LLM_TIMEOUT
  code / kind timeout / recoverable。
- **MCP request**：`mcp_client` RAGENT_MCP_TIMEOUT(15s) retry(1) 仅 TIMEOUT → classify_mcp_error。
- **DB statement**：`sql_tools` STATEMENT_TIMEOUT_MS(30s) → SQL kind timeout →
  DiagnosePolicy `not agent_recoverable → fail`（盲 retry 无意义）。
- **background task**：`MAX_TASK_DURATION`(600s) → asyncio.TimeoutError → persist FAILED +
  TASK_TIMEOUT SSE。
矩阵契约钉 `LAYER_DEFAULTS` 一致性 + 每类 timeout 的 classification 映射 + retry/no-retry
判定（消费 errors.py / retry.py / timeout.py 同源，不新写行为）。

### ⑧ background-task timeout E2E（P1，live，独立文件）

宪法 §11「后台任务超时 → Persist FAILED → ReportVersion(error)」，与 sse_disconnect（断连≠
后台失败）正交。`MAX_TASK_DURATION` 是 import 时模块常量，无法按请求压短 → 起**第二 backend
实例** :8101 带 `MAX_TASK_DURATION=5`（chat 需求分析不走 run_with_timeout，不受影响；confirm
正常 30-90s 必然超 5s）。新文件 `evaluation/tests/test_background_timeout_e2e.py`：gate
`REPORTAGENT_E2E=1` + `REPORTAGENT_E2E_TIMEOUT_BASE_URL`（指向低超时实例；未设 skip）。
断言：confirm SSE 出 error `TASK_TIMEOUT` → 轮询 session `current_phase != generating`（error
终态）→ report_version 落库 error（FAILED/error，不停 generating）。

### ⑨ DB timeout contract（P2，offline，不真等 30s）

`execute_sql` 层 override statement timeout 为 0.05s/0.1s（monkeypatch sql_tools 常量），真跑
一条慢查询（pg_sleep / 大聚合）→ 断言分类 `kind=timeout` → DiagnosePolicy `fail`（非
recoverable）→ 无假行。另钉 connection timeout 覆盖路径（若可低成本触发）。

## Files to change（文件改动）

| 文件 | 变更 | 项 |
|---|---|---|
| `evaluation/tests/test_real_rag_mcp_e2e.py` | gate `!= "1"`；`_patch_fill_all` raise_for_status | ①④ |
| `evaluation/tests/test_real_rag_mcp_edge_cases.py` | gate `!= "1"`；mcp_down_execution 改 obscure 对比 + 收敛 | ①③⑤ |
| `backend/tests/contracts/test_mcp_down_seam.py` | async create_task 继承契约（正/反） | ② |
| `backend/tests/contracts/test_timeout_policy.py` | 新：timeout→classification→retry→terminal matrix（LLM/MCP/DB/background） | ⑦ |
| `backend/tests/contracts/test_mcp_error_business_matrix.py` | 新：MCP 三码业务层 matrix（分类/dispatcher/no-match 独立） | ⑥ |
| `backend/tests/contracts/test_db_timeout_contract.py` | 新：短 statement timeout override → kind=timeout → fail | ⑨ |
| `evaluation/tests/test_background_timeout_e2e.py` | 新：低 MAX_TASK_DURATION 实例 → TASK_TIMEOUT 断言 | ⑧ |
| （可能）`evaluation/tests/test_real_rag_mcp_edge_cases.py` input | double_fact / mcp_down query 调优 | ③⑤ |

## Reused existing utilities（复用工具）

- `classify_mcp_error` / `classify_exception` / `_USER_CODES` / `user_recoverable`（errors.py）——⑦⑥ 同源
- `LAYER_DEFAULTS` / `run_with_timeout`（timeout.py）；`RETRY_BUDGETS`/`compute_backoff`（retry.py/backoff.py）
- `rag_schema._retrieve_dict` dispatcher + `_validate_matches_contract`（⑥ 现状已钉，只收 matrix）
- `evaluation/tests/test_real_rag_mcp_e2e.py` drivers（login/SSE/fill/confirm）——③⑧
- `mcp_down` seam（contextvar + scoped）——③（已上线 `6dbfd8a`）
- 第二 backend 实例启动脚本模式（本 session 已用 nohup + REPORTAGENT_E2E=1）——⑧

## Verification（验证）

- ①②④⑥⑦⑨：offline `pytest tests/contracts tests/smoke`（增量）；新增各自文件独立绿。
- ③⑤：live（REPORTAGENT_E2E=1 + :8100 + ragent MCP + PG）——③ obscure 2× 稳定；
  ⑤ edge 全文件 3~5 次连跑观察 double_fact；⑧ :8101 低超时实例独立跑。
- 全程不动 master（工作分支 `p15-e2e-live`），按用户顺序逐 commit 可追溯。

## Explicitly NOT doing（不做事项）

- **不**继续加业务 e2e case（用户明示够多）；不扩 fault seam kind（⑦ 已自评够）。
- **不**改工具契约扁平（schema 检索失败→[] 是故意降级，理由见 ⑥）；不做「MCP-down → 强制
  execution 失败」的产品改动（memory 兜底 SUCCESS 是合法诚实终态，已实测并断言允许）。
- **不**引入 Prometheus/Grafana 或新可观测 sink（日志 marker 只在 ③ 回退方案用到）。
- **不**为 ⑨ 真等 30s（override 短超时）；不为 ⑧ 等 600s（低超时第二实例）。
- **不**动 ragent-py（独立 repo）；不动 P12/P13 已冻结前端契约。
