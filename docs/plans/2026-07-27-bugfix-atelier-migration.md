# Plan: SQL 空数据 Bug 修复 + Atelier 替换 antd（阶段 A–C）

## Context

分支 `feat/atelier-demo`（= `origin/master`，领先本地 master 18 个提交）承载两条工作线：

1. **Bug**：hand-off §3.1/§3.3 —— `POST /confirm` 后 v1 报告为 `chart={type:table,config:{}}`、`table=null`、`insight=null`、`query_snapshot=null`、`trace=[]`。根因已证实（4 个独立成因）：
   - **think 块污染**：reasoning 模型（MiniMax-M2.7）在输出前包裹思考块标记（开始/结束 tag），`_generate_sql` 只跑 `strip_markdown_fence`，思考散文被喂给 sqlglot/关键字校验 → 重试 3 次耗尽 → 子图静默返回空。工作区 WIP 已修闭合块，但未闭合（token 预算耗尽截断）仍漏。
   - **planner 信号空**：confirm 时 `user_query=""`（`main.py:691`），需求卡只是 planner 的弱提示；`_format_confirmed_requirement` 无字段时返回 None → planner 空跑。
   - **报告载荷硬编码**：`confirmed_execution_graph.py:224-235` 硬编码 `table: None` / `trace: []` / 无条件 `"SUCCESS"`，即使 SQL 成功也无 table，失败被伪装成功落库。
   - **e2e 门太弱**：`test_full_flow.py` 被放宽为接受 `{type:table,config:{}}`，bug 活着时测试也绿。
2. **迁移**：`docs/atelier/MIGRATION.md` 定义 antd → atelier 的 A–D 阶段，全部 TBD。demo（`docs/atelier/`，73+ 纯 HTML/CSS/JS 组件，Chrome 实测无控制台错误）是移植源；`adapters/antd/` 是零引用的过渡死代码（Tooltip.tsx:4 循环类型会炸 `tsc -b`、Tag 把 `color="teal"` 映射成 neutral、Spin 无样式）——**C 阶段末整体删除**。

工作区 9 个未提交文件正确修复了主触发器（think 块剥离 + max_tokens 1500 + PATCH/422 死锁），前后端互相一致，保留并加固。

**用户指令**：先修 bug，再按分支计划迁移，每步测试先行。**范围**：Bug 轨全部 + 迁移 A–C（真实 atelier 组件**替换** antd）；阶段 D（删 legacy、卸 antd 依赖、Form 校验迁移）另行计划。

## 已决设计

| 决策 | 结论 |
| --- | --- |
| 组件策略 | **真实替换**：`docs/atelier/atelier.css`（命名空间 BEM）整体移植为 `frontend/src/components/atelier/atelier.css` 全局样式，删 gallery 专用段；A–C 页面需要的组件逐个实现为真实 React 组件（状态化、portal、键盘可达），页面直接 import。antd 仅保留 `Form.useForm+rules` 校验壳（LoginPage/TemplateLibraryPage，MIGRATION §10 明确留到 D）与 ConfigProvider 外壳 |
| 过渡层 | `adapters/antd/` 仅 B 阶段容许短暂兜底未移植件；**C 末删除整个目录**，验收门 = 全仓 grep 无非 Form 路径的 antd import |
| Token 命名 | 前端约定为准（`--sp-xs..2xl`、`--r-xs..l`）；移植 demo CSS 时重写变量名 |
| Token 补齐 | 从 `docs/atelier/tokens.css` 移植缺项：`--sp-20: 20px`、`--sp-40: 40px`、`--shadow-focus`、`--topbar-h: 58px`、`--left-rail-w: 248px`、`--right-rail-w: 300px`、`--content-max: 1180px`、`--t-fast/base/slow`；新增 `--on-ink: #fff` / `--on-ink-2: rgba(255,255,255,.85)` / `--on-ink-3: rgba(255,255,255,.65)` 消灭页面 8 处裸白 |
| 提交 | 每阶段完成后**提议**拆分提交（用户批准才提交）；先合 bug 轨，再逐阶段合迁移 |

## 组件移植清单（按阶段）

| 阶段 | 新组件（测试先行） | 消费页面 |
| --- | --- | --- |
| 5 | `Button` / `Tag` / `TextField` / `TextArea` / `Spinner` / `Empty` / `Toast`+`useToast`+`ToastProvider` | 全部 |
| 6 | （Stage 5 件即用） | LoginPage |
| 7 | `RadioGroup kind="pill"` / `CheckboxGroup` / `Dropdown`（键盘导航）/ `Avatar` / `TopBar` / `ProgressCircle`+`Stepper` / `RequirementCard` 壳 | WorkbenchPage / RequirementCardView / ReportPaper |
| 8 | `Modal`（portal + `useFocusTrap`，焦点逻辑从 `docs/atelier/atelier.js` `openModal` 移植）/ `Popconfirm` / `Tooltip` | TemplateLibraryPage / SecureReportPage |

每个组件的移植规范：props API 以 MIGRATION §1 映射表为单一来源（`variant`/`tone`/`open`/`actions`/`startAdornment`/`kind="pill"` 等）；视觉以 `docs/atelier/index.html` demo 为准；a11y 以 MIGRATION §7 键盘表为准（Modal 焦点圈 + 焦点回退、Dropdown ↑↓/Enter/Esc、Toast `aria-live="polite"`）；`Toast` API 对齐 `docs/atelier/atelier.js:48-63`（`toast.success/error/warning/info`）。antd 组件剩余用法（无对应真实组件时）临时走适配器兜底，并在该组件行登记"待移植"。

---

## Stage 0 — 测试体系守卫（先行，使"测试先行"可执行）

现状坑：`pytest.ini` 声明 5 个 marker 但只有 `persistence` 被实际应用；e2e 无 marker 无守卫，离线裸跑 `pytest` 必挂。

- 为现有 5 个测试文件补 `pytestmark`：`smoke/test_models.py`→smoke、`contracts/…`→contracts、`graphs/…`×2→graphs、`e2e/test_full_flow.py`→**新 marker `e2e`**（注册进 pytest.ini）
- `backend/tests/conftest.py`：仿 persistence 自动跳过模式，`e2e` 测试在 `REPORTAGENT_E2E` 未置位时自动 skip
- 验证：`cd backend && pytest` 离线 0 失败（e2e/persistence skipped）；`pytest -m graphs` / `-m smoke` 能选中用例

## Stage 1 — 固化 SQL 修复（测试先行）

- **先写** `backend/tests/graphs/test_sql_generation.py`（marker graphs）：monkeypatch `call_llm`（照 `test_requirement_parser.py` 模式，同时 patch 消费模块与 `app.llm`）返回 ①闭合思考块+SQL ②**未闭合**思考块 ③纯 SQL ④截断无 SQL，驱动 `build_sql_graph()` 断言 `state["generated_sql"]`
- **sanitize 集中化**：新增 `strip_think` / `extract_sql` 到 `backend/app/utils/text.py`（与已提交的 `safe_json_parse` raw_decode 扫描同文件），`_plan` 与 `_generate_sql` 共用；`_generate_sql` 同时处理未闭合前缀（按开始 tag 切分取尾部 / strip 至首个 SELECT），保留 `"select" not in → ""` 兜底。删除 WIP 的内联正则
- **planner 信号加固**：在 `confirmed_execution_graph.py` 内从确认卡合成权威自然语言查询（time_range/scope/metrics/dimensions/methods/accepted assumptions），作为 `_plan` 主信号（`user_query` 为空时）；图测试断言 `_plan` 收到非空权威查询
- 修 WIP 死代码：`backend/app/services/requirement_service.py:152-184` —— 从 `requirement_repository` import `LockError`，`try/except LockError` 包住 `lock_draft` 使陈旧锁恢复可达，删掉会 NameError 的裸 `raise LockError`
- 保留 WIP 其余改动（max_tokens 1500、validator 放宽 + service 重算 status、前端 SSE `\r\n` 兼容、canConfirm 门控）——已确认前后端一致
- 验证：`pytest --ignore=tests/e2e` 全绿

## Stage 2 — 真实 `answer.table` + 失败显性化（测试先行）

- **先写** `backend/tests/graphs/test_confirmed_report_agent.py`（marker graphs）：stub `build_report_graph` 返回，直接调 `_confirmed_report_agent`：有 `query_result` → `answer.table` 含 columns/rows、`execution_status="SUCCESS"`；空结果 → `"FAILED"`
- 改 `confirmed_execution_graph.py:224-235`：`table` 从 `state["query_result"]` 构建（参照 `main.py` 旧版 `_build_response` ~L616 表格装配）；`execution_status` 仅在 query_result 有行时 `SUCCESS`
- `main.py` `/confirm` SSE：`FAILED` 时发 SSE v2 `error` 事件 `{code, message, recoverable, failed_action:"confirm"}`，不发假 `report`
- 前端（测试先行）：`analysisReducer` confirm 流 `error` 事件 → phase `error` 用例；`WorkbenchPage` 重试入口（对接已有 `POST /sessions/{sid}/retry`）。注意 `reportAdapter.ts` 仅在 `answer.table` 有行时渲染表格——Stage 2 修复后自然生效，补一条 adapter 用例锁死
- 验证：vitest + pytest 全绿

## Stage 3 — e2e 锁死 + 全栈验收

- 收紧 `test_full_flow.py` 第 6 步核心断言：`query_snapshot != null` 且 `.sql` 非空；`answer.table != null` 且有行；不再接受 `{type:table,config:{}}` 退化形态
- 全栈跑（真实 LLM key）：PG 容器 + `init_pg.sql`/`seed_pg.sql` → MCP server → `uvicorn :8100` → 仓库根 `python -m pytest backend/tests/e2e/test_full_flow.py -s`
- 至此 bug 轨收口，**提议提交**（拆 4 个：sanitize 集中化+planner 信号+validator/service、answer.table+失败显性化、测试守卫、e2e 收紧）

## Stage 4（次级，可延后）— trace 管线

- `SQLAgentState`/`ReportAgentState` 补 `trace_id` 键（现状被 LangGraph 丢弃 → 子图 span 进永不 flush 的幽灵 tracer）；`_confirmed_sql_agent`/`_confirmed_report_agent` 已传入 `trace_id`（L222），补键即生效
- `report_payload.trace`：从 tracer span 摘要填充，或从契约删除（同步 `frontend/src/types/*` + contracts 镜像测试）

---

## Stage 5 — 组件库地基（测试先行，首批 .tsx 测试）

- **先写** `frontend/src/components/atelier/__tests__/` 首批用例（RTL 已装但零先例，此批立规：`render` + `screen` + `fireEvent`，a11y 用 `getByRole`）：Button `variant=primary` 类名与点击、Tag `tone` 全映射、TextField 受控输入、Spinner/Empty 渲染、Toast provider 触发后 `aria-live` 容器出现文案
- tokens 补齐（按"已决设计"表改 `frontend/src/styles/tokens.css`）
- 移植 `docs/atelier/atelier.css` → `frontend/src/components/atelier/atelier.css`（删 gallery 段），`main.tsx` 引入
- 实现首批真实组件：`Button.tsx` / `Tag.tsx` / `TextField.tsx` / `TextArea.tsx` / `Spinner.tsx` / `Empty.tsx` / `Toast.tsx`（provider + useToast）
- `App.tsx`：`ConfigProvider` 内包 `ToastProvider`（`AntdApp` 暂留，随最后一个 `App.useApp()` 消亡而删）
- 验证：vitest（含新 .tsx）+ `npx tsc -b` + `npx oxlint`

## Stage 6 — 阶段 A：LoginPage（测试先行）

- **先写** `LoginPage.test.tsx`：渲染 → 填表提交 → mock `authStore.login` → 成功 toast 出现；用户名为空 → 校验提示
- `LoginPage.tsx`：atelier `Button` + `TextField`（含 password 型）；`App.useApp()+message` → `useToast()`；`Form`/`Typography` 保留 antd（D 再动）；已零 hex ✓
- 验证门：vitest + tsc + oxlint + 手动登录走通

## Stage 7 — 阶段 B：WorkbenchPage + RequirementCardView + ReportPaper（测试先行）

- **先写** `RequirementCardView.test.tsx`：pill RadioGroup 切换 → `onChange` 载荷含 `selected_value`；`canConfirm` 门控；assumption 接受/拒绝按钮。`TopBar`/`Dropdown` 键盘用例
- 新组件：`RadioGroup kind="pill"`（demo `.atelier-radio-pill`）/ `CheckboxGroup` / `Dropdown` / `Avatar` / `TopBar` / `ProgressCircle`+`Stepper`（MIGRATION §4 进度区）
- `RequirementCardView.tsx`：Radio/Checkbox 组换真实组件；`STATUS_COLOR` 已是 atelier 名，`Tag` 直接用；message → useToast
- `WorkbenchPage.tsx`：`Layout.Header` → `TopBar`；Dropdown/Avatar/Tag/Empty/Spinner/TextArea 真实组件；`#FFFFFF`×3+rgba×2 → `--on-ink*`；message 5 处（含 MsgApi shim）→ useToast；进度区 → ProgressCircle+Stepper
- `ReportPaper.tsx`（MIGRATION 误称"已无 antd"，实测仍引 Empty/Spin/Tag/Typography）：换真实组件
- 验证门：vitest + tsc + oxlint + 手动 1440/1180/880 三档

## Stage 8 — 阶段 C：TemplateLibraryPage + SecureReportPage + 删适配层（测试先行）

- **先写** `TemplateLibraryPage.test.tsx`：mock fetch → 列表渲染；新建 Modal 提交走 `templatesClient`；Popconfirm 删除；Modal Esc 关闭 + 焦点回退触发器
- 新组件：`Modal`（useFocusTrap 移植自 atelier.js openModal）/ `Popconfirm` / `Tooltip`
- `TemplateLibraryPage.tsx`：Modal/Popconfirm/Spin/Tag/Empty/Button 真实组件；`Input.Search` → TextField；`#FFFFFF`×4 → token；Layout → 语义 header/main。`List` → 语义列表 + atelier CSS；`Form` 校验壳保留 antd
- `SecureReportPage.tsx`：Layout → header/main；Spinner 真实组件；`#FFFFFF`×2 → token
- `HistoryPage.tsx`（legacy 路由，仅灭毒不迁组件）：`#1677ff`/`#f5f6f9`/`#e8e8e8`/`#999` 全换 token
- **删除 `frontend/src/components/atelier/adapters/antd/` 整个目录**
- 验收门：全仓 grep `from 'antd'` 只剩 Form/Form.Item/useForm + ConfigProvider/App 外壳；antd import 逐个列入 MIGRATION §10 清单
- 文档收口：`MIGRATION.md` §9 进度表填真实 commit、修正 §2 对 ReportPaper/SecureReportPage 的两处误述；`docs/hand-off.md` §3 逐条勾掉；CLAUDE.md 补 atelier 组件库约定（真实组件 + tokens 单一来源 + antd 仅限 Form 壳）
- 可选浏览器门：`frontend/package.json` 加 `playwright` devDep，两个 browser 脚本硬编码 Edge 路径改 channel/env 可配，手动跑 `node browser_test_query.mjs`（`probe.mjs` 已坏，删或修）

## 全量验证清单（每阶段收口执行相应子集）

```bash
cd backend && pytest                                # 离线全量（e2e/persistence 自动 skip）
cd backend && pytest -m persistence                 # 需 ragent-postgres 起着
# 仓库根，全栈 + 真实 LLM key：
python -m pytest backend/tests/e2e/test_full_flow.py -s
cd frontend && npm run test:run                     # vitest 单次
cd frontend && npx tsc -b && npx oxlint             # 类型 + lint
# 手动：登录 → 提问 → PATCH → 确认执行 → v1 报告有表有图；1440/1180/880 三档视觉
```

## 明确排除（本计划不做）

- 阶段 D：删 `/legacy/*`、卸载 antd 依赖、`Form.useForm+rules` → react-hook-form、`Select`/`useListbox`、DatePicker（A–C 验收后另行计划）
- 像素基线自动化（`baseline/` capture/check 工作流缺口仅记录）、CI/CD、i18n、production PostgresSaver

## 附注

规划期间另有一份更长的独立计划（`C:\Users\Lenovo\.claude\plans\nested-chasing-cook-agent-aef7d840d0b1b9272.md`，约 1800 行，含逐任务 red-green-refactor 细节），其 bug 轨结论与本计划一致（sanitize 集中化、planner 信号合成已并入 Stage 1）；但其迁移部分主张"适配器优先"，**已被用户决策推翻**——以本文件的"真实替换"为准。
