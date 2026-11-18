> 状态: 已完成（2026-11-18）

# 夜间 e2e-contract fixture 重对齐（连红 7 天的根因修复）

## Context

夜间 `e2e-contract.yml` 自 2026-11-10 起**连续 7 次失败**（run 16–22），无人发现——
这条流水线要么修绿，要么等于没有（用户 2026-11-18 的判断：「CI 用处不大，测试的例子不是实时的」，
这条死红就是最直接的证据）。

只读诊断（同批 plan `2026-11-18-live-ci-pipeline` 的 task-1）结论：

- **根因**：`7a9092b`（2026-11-11，`fix(sql): keep required filters out of outer-join on`）在
  `sql_graph._validate` 新增确定性闸门 C1「plan 要求的过滤取值必须真的出现在 SQL 里」；
  但 `backend/tests/fixtures/llm_responses/` 里 3 个 case 的 canned SQL 仍是加闸前的写法（缺 `2024` 年份谓词）。
- **失败机制**：confirm 阶段 → C1 判违约 → 进 repair 去取 `sql_generate:2/:3` → fixture 没有该 key →
  `MockLLMMiss` → 任务以 `phase=error` 收尾 → `04-empty-result`（EMPTY band 不出现）、
  `06-report-version`（v2 报告不出现）、`07-background-execution`（phase 不是 report_ready）三条 spec 必红。
- **为什么是现在**：run 15（`3278bff`，含闸门之前）绿；run 16 起都含 `7a9092b`。
  本地在 run 22 同 sha（`8ca0f6e`）复现出**完全相同的三条红**，backend stderr 里可直接看到
  `required filter missing, routing to repair: 缺少用户要求的过滤条件 2024` + `MockLLMMiss key=sql_generate:2`。

这是**测试夹具漂移**，不是产品缺陷：闸门本身是对的（防「过滤写在 ON 上静默不过滤」这类错数）。

## Design

1. **只改夹具，不改产品代码**：把 3 个 fixture 的 canned SQL 补齐 `dim_date` 联接 + `d.year = 2024`
   谓词，保持原测试意图（EMPTY / v2 报告 / 后台执行完成）不变；
2. **加防漂移闸**：新增契约测试，遍历 `backend/tests/fixtures/llm_responses/*.json` 的
   `sql_generate*`，用**同一条** `_missing_filter_violation` 判定 canned SQL 是否满足其 `sql_plan` 的必需过滤。
   以后再加确定性 SQL 契约时，fixture 不同步会**先在 backend pytest 里红**（L1 门禁），
   而不是等夜间 E2E 红、再没人看。
3. **不做** mock keying 的「kind 优先 + 轮转」兜底（诊断建议里的 A 方案）：它不会让这三条变绿，
   只会把 `MockLLMMiss` 换成难查的业务红，并遮蔽夹具缺口——留作后续单独评审。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/tests/fixtures/llm_responses/empty-result.json` | `sql_generate:1` 补 `JOIN dim_date` + `d.year = 2024`（保留 `AND 1 = 0`，仍返回 EMPTY） |
| `backend/tests/fixtures/llm_responses/report-version.json` | `sql_generate:2` 补 `JOIN dim_date` + `d.year = 2024` |
| `backend/tests/fixtures/llm_responses/background-execution.json` | `sql_generate:1` 补 `JOIN dim_date` + `d.year = 2024`（保留 `s.region = '华南'`） |
| `backend/tests/contracts/test_llm_mock_fixture_contract.py` | 新增：防漂移闸（fixture 的 SQL 必须满足 C1） |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `backend/app/agent/sql_graph.py::_missing_filter_violation` / `_required_filter_values`：**不重写**判定，
  测试直接调用同一条函数（否则闸门改了口径测试还是绿的）；
- `backend/app/agent/sql_graph.py::SQLPlan`（或 plan 的 pydantic 模型）：用真实模型 `model_validate`，
  从 `sql_plan:N` 取 filters；
- `MockLLMMiss` 的日志与 `LLM_MOCK_CASE`/`LLM_MOCK_DIR` 约定不变。

## Verification

1. `pytest backend/tests/contracts/test_llm_mock_fixture_contract.py -q` → 全绿（且**故意**去掉一处
   `d.year = 2024` 时该测试会红，证明闸门有效）；
2. 本地跑三条 spec：
   ```powershell
   cd frontend
   $env:RAGENT_PYTHON='D:/miniConda/envs/agent/python.exe'
   npx playwright test --config e2e/playwright.config.ts `
     e2e/specs/04-empty-result.spec.ts e2e/specs/06-report-version.spec.ts e2e/specs/07-background-execution.spec.ts
   ```
   期望 **3 passed**，且 backend stderr 不再出现 `required filter missing` / `MockLLMMiss`；
3. 全量 10 条 contract（`npm run e2e:contract`）转绿；
4. 推送后下一次夜间/手动触发 `e2e-contract` 确认转绿。

## Explicitly NOT doing

- 不改 `sql_graph` 的 C1 闸门（它是对的，红是夹具没跟上）；
- 不做 mock keying 兜底/轮转（A 方案，单独评审）；
- 不缩 spec 子集、不改断言（B/C 方案 = 把核心契约测没了）；
- 不处理 run 21 的 spec 10 偶发（时序问题，另案）。

## 落地结果（2026-11-18）

| 项 | 结果 |
|---|---|
| 3 个 fixture 的 canned SQL | 已补 `JOIN dim_date d ON d.full_date = o.order_date` + `d.year = 2024`（`empty-result` / `background-execution` / `report-version`） |
| 防漂移闸 | 新增 `backend/tests/contracts/test_llm_mock_fixture_contract.py`：直接调同一条 `_missing_filter_violation`；**实测抽掉年份谓词 → 1 failed，还原 → 1 passed**（闸门确实会咬） |
| 反向闸 | `_INTENTIONALLY_BROKEN` 里的 5 条（failed-result ×3 / retry:1 / replay-object-not-found:1，都是「坏 SQL 本身是被测对象」）必须仍然违约；夹具修好后要删白名单，避免豁免变成历史垃圾 |
| 三条曾红的 spec | `04 / 06 / 07` → **3 passed (1.3m)**，backend stderr 不再出现 `required filter missing` / `MockLLMMiss` |
| 全量 Contract | **10 passed (4.1m)**（本机，真 PG + mock LLM，与 CI 同配置） |
| 远端 | 已推送 `854fabd`；另给 `e2e-contract` 加了「定时失败自动开 Issue」（这条连红 7 天没人看的另一半根因） |

**顺带发现的潜在风险（未修，留档）**：mock 阶段 `report narration` 会打印
`无法从 prompt 识别语义 kind（no marker matched）` —— 报告叙述 prompt 在 mock 里没有对应 kind marker，
真触发时会静默降级。属 mock 覆盖缺口，不是本次红的成因，另案处理。
