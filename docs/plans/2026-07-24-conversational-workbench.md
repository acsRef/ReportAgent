# ReportAgent 对话式分析与全应用重构计划

## Context

`docs/intelligent-analysis-workbench.html` 已经确认了 ReportAgent 的目标产品体验：用户问题不能直接触发 SQL 和报告；系统必须先用 LLM 结合 Schema/Tool 元数据形成结构化需求卡，缺失时补充、完整时确认，只有用户主动确认后才执行 Data → SQL → Report。报告完成后，用户可继续对话生成同一会话下的 v2/v3，切换历史版本不再调用 LLM。

本次项目改造要把该原型完整落地到 React、FastAPI/LangGraph 和 PostgreSQL，并同时将模板中心改为后端持久化。需求草稿、会话阶段、报告版本和模板必须在服务重启后可恢复。现有 `MemorySaver` 只作为单次图执行的开发检查点；跨 HTTP turn 的业务状态以 PostgreSQL 为准，不依赖内存 checkpoint。

关键约束：

- 顶部仅保留“工作台 / 模板中心 / 已连接 / 用户”，不增加数据域、数据目录或数据源选择。
- Tools 是 Agent 内部能力，业务用户只看到“趋势分析、贡献分析、异常检测”等业务方法。
- 每个新分析都必须确认；需求不完整时由后端返回 `missing_fields/options/assumptions`。
- 确认前允许 Schema 元数据发现，但严禁调用 `validate_sql`、`execute_sql` 或 Report Agent。
- 报告调整以已确认需求和指定基线版本为上下文，直接生成下一版本；旧版本保持不可变。
- 当前机器没有可用 Python 后端环境或前端依赖环境；本阶段只做静态设计与代码逻辑审查，不安装依赖、不启动服务、不执行数据库迁移。
- 后续即使静态实现代码，也必须如实标注“未运行验证”；测试与验证命令保留给具备环境的机器执行。
- 当前无测试基础，计划中补齐最小 pytest/Vitest，但不能在本机声称测试通过。

## Recommended Architecture

## 1. Separate the requirement and execution turns

保留 `POST /api/v1/chat` 作为自然语言入口，但不要用一个包含大量 `action` 的请求模型承载所有行为。采用职责清晰的 API：

- `POST /api/v1/chat`（SSE）
  - 新问题：`mode="new"`
  - 自由文本补充：`mode="supplement"`
  - 报告调整：`mode="adjust"`，携带 `base_report_version`
- `PATCH /api/v1/sessions/{session_id}/requirement`
  - 只更新选项与 Agent 假设，返回更新后的 RequirementCard JSON；不运行分析图。
- `POST /api/v1/sessions/{session_id}/confirm`（SSE）
  - 服务端重新校验需求完整性，锁定需求后运行 Data → SQL → Report。
- `POST /api/v1/sessions/{session_id}/retry`（SSE）
  - 按持久化的失败操作恢复，不创建新会话。
- `GET /api/v1/sessions`
  - 返回分组左栏所需的会话阶段、更新时间、消息数、报告版本摘要。
- `GET /api/v1/sessions/{session_id}`
  - 返回会话消息、当前需求卡、报告版本摘要和最后失败操作。
- `GET /api/v1/sessions/{session_id}/reports/{version}`
  - 读取指定版本报告；纯数据库读取，不执行 LangGraph/LLM。
- `/api/v1/templates` CRUD
  - 模板保存已确认 RequirementCard 配置，不保存静态 ReportBlock。

这组 API 明确区分“自然语言对话”“结构化补丁”“确认执行”和“历史读取”，避免当前 `chosen_tool`/interrupt/update_state 的隐式行为。

## 2. Backend state machine and SQL gate

在 `backend/app/agent/parent_graph.py` 中将当前 intent/options/clarify 路径收敛为两个明确入口：

### Requirement-analysis run

```text
security_guard → classify_intent → data_agent(schema only) → requirement_parse → END
```

- `data_agent` 在此阶段只能使用 `search_tables/get_table_ddl/list_tables` 等 Schema 工具。
- `requirement_parse` 使用 LLM 结构化输出生成 RequirementCard，结合：
  - 用户问题与已有补充；
  - `SchemaContext` 中可用表和列；
  - ToolRegistry 的分析能力描述；
  - 服务端安全的选项提供器。
- 完整需求持久化为 `awaiting_confirm`；缺失需求持久化为 `awaiting_missing`。
- 该图中不注册/不暴露 `validate_sql`、`execute_sql` 和 Report tools，从结构上保证确认前不能查询业务数据。

### Confirmed-execution run

```text
load_confirmed_requirement → data_agent(refresh schema) → sql_agent → evaluate/retry → report_agent → persist_report → END
```

- `/confirm` 从 PostgreSQL 加载且锁定最新 RequirementDraft。
- 在进入 `sql_agent` 前增加硬 gate：draft 必须属于当前用户/会话，状态为 `confirmed`，且 `missing_fields == []`。
- `sql_graph.py` 接收已确认的 `QueryPlan/RequirementDraft`，不再先发 `intent_card` 或等待 `chosen_tool`。
- SQL 重试和 ReportBlock 生成继续复用现有 SQL/Report 子图、三层 SQL 校验和 ReportRenderer 所需响应。
- 调整报告时加载 `base_report_version` 与其 requirement snapshot，将用户调整解析成新执行上下文；成功后追加新版本，失败不污染旧版本。

`clarify` interrupt 和旧 `intent_card/options_group/chosen_tool` 保留一个兼容周期，仅服务旧会话/旧前端；新工作台不依赖它们。完成兼容验证后再删除。

## 3. RequirementCard shared contract

后端在 `backend/app/models/contracts.py`（或相邻 `models/requirement.py`）定义 Pydantic 模型，前端在 `frontend/src/types/requirement.ts` 手工镜像。先不引入 schema-to-TS 生成工具。

```text
RequirementCard
- id: string                        稳定 ID
- version: integer                  每次后端补丁递增
- status: missing | complete | locked
- summary: string                   一句话业务目标
- target_metrics: string[]
- time_range: string | null
- scope: string[]
- dimensions: string[]
- analysis_methods: string[]        业务名称，不暴露 tool key
- expected_blocks: string[]
- missing_fields: RequirementMissingField[]
- assumptions: RequirementAssumption[]
- confidence: number
- confirmed_at: datetime | null

RequirementMissingField
- key: time_range | scope | metric | comparison | granularity
- label: string
- kind: single | multiple
- options: [{label, value}]

RequirementAssumption
- key: string
- text: string
- accepted: boolean | null
- alternatives: [{label, value}]
```

不让 LLM 直接发明所有选项：

- 时间快捷项由服务器规则生成，并根据当前日期和 `dim_date` Schema 能力校验。
- region/metric/channel 等候选由 `requirement_options.py` 根据 SchemaContext 与受控业务语义映射生成；第一版可复用当前 `_build_options_group_card` 的时间/区域/指标选项，但迁出 graph 并集中测试。
- `analysis_methods` 从 ToolRegistry metadata 映射成业务名称，LLM 只选择允许集合。
- PATCH 后由服务器重算 `missing_fields/status/version`；前端不能自行把卡片标记 complete。

## 4. PostgreSQL persistence

在 `backend/scripts/init_pg.sql` 增加三张最小表，并通过 asyncpg repository 访问：

### `agent.requirement_draft`

- `id BIGSERIAL PK`
- `session_id VARCHAR(64)`
- `version INT`
- `user_query TEXT`
- `status VARCHAR(32)` (`missing/complete/confirmed/abandoned`)
- `payload JSONB`（完整 RequirementCard）
- `confirmed_at`, `created_at`, `updated_at`
- unique `(session_id, version)`

### `agent.report_version`

- `id BIGSERIAL PK`
- `session_id VARCHAR(64)`
- `version INT`
- `parent_version INT NULL`
- `requirement_draft_id BIGINT`
- `adjustment_text TEXT NULL`
- `title TEXT`
- `status VARCHAR(32)`
- `report_payload JSONB`（前端 ReportResponse/ReportBlock 可恢复快照）
- `query_snapshot JSONB`（SQL、columns、rows、error；只对授权用户返回）
- `trace_id VARCHAR(64)`
- `favorite BOOLEAN DEFAULT FALSE`
- `created_at`
- unique `(session_id, version)`

### `app.report_template`

- `id BIGSERIAL PK`
- `user_id INT FK app.users`
- `name`, `description`
- `requirement_payload JSONB`
- `created_at`, `updated_at`
- unique `(user_id, name)`

不增加独立 adjustment 表；调整文本和 parent_version 放在 report_version 即可。`app.conversations.metadata` 只保存引用信息（requirement version、report version、card snapshot），不承载整个会话状态。

扩展 `agent.session`：

- 使用现有 `status` 保存当前 phase；允许 `idle/parsing/awaiting_missing/awaiting_confirm/generating/adjusting/report_ready/error`。
- 修复 `main.py` 创建 session 时把 `request.session_id` 误当 `user_id` 的 bug，使用 JWT 用户 ID。
- 可选增加 `latest_requirement_version` 和 `latest_report_version`，便于列表查询；也可由 repository join/max 得到，首版优先查询得到，避免冗余。

每次写 requirement/report/conversation message 使用同一 asyncpg transaction。报告版本号通过事务内 `MAX(version)+1` 并结合 unique constraint 处理并发。

跨重启恢复来自这些表，而不是 `MemorySaver`。生产 PostgresSaver 仍作为后续基础设施事项记录，不阻塞本次业务恢复。

## 5. SSE v2 and backward compatibility

扩展前端 `SSEEventType` 与后端 formatter：

- `phase`: `{phase, reason?}`，为 UI 阶段权威来源。
- `requirement`: 完整 RequirementCard。
- `trace`: 继续复用现有进度事件。
- `thinking`: 解析/执行提示。
- `report`: `{version, parent_version, title, answer, trace}`。
- `error`: `{code, message, recoverable, failed_action}`。
- `done`: `{final_phase}`。

旧 `card/clarify/token` 保留兼容：

- 新 reducer 可解析旧 card，并映射成只读旧消息；不再用其驱动新流程。
- 后端仍可为旧客户端发送 legacy `card`，但新客户端以 `phase/requirement` 为准。
- 旧会话没有 requirement/report_version 时，只显示历史消息；第一次继续分析时建立新 RequirementDraft。

## 6. Frontend single-phase architecture

用一个 `AnalysisPhase` 替换当前 `viewMode + intentPhase + busy` 三套可写状态：

```text
idle | parsing | awaiting_missing | awaiting_confirm |
generating | adjusting | report_ready | error
```

`busy` 从 phase 派生：`parsing/generating/adjusting`。完整报告不是独立业务 phase；“聚焦报告”只是本地 UI 偏好。

将 SSE 归约抽到纯函数 `frontend/src/stores/analysisReducer.ts`：

- `phase` → 更新 phase；
- `requirement` → 替换服务端权威卡片；
- `trace` → upsert timeline；
- `report` → append immutable ReportVersion 并选中该版本；
- `error` → error phase；
- `done` → 结束流，不擅自重置 phase。

将当前 `stores/session.ts` 拆为：

- `stores/analysisStore.ts`：当前会话、phase、requirement、versions、timeline、error、SSE actions。
- `stores/templateStore.ts`：模板 CRUD（改用 API，不再 localStorage）。
- `stores/authStore.ts`：保持。

只把 session ID/auth 和纯 UI 偏好放 localStorage；会话业务状态全部从 API snapshot 恢复。

## 7. React component migration to the approved prototype

新增/重组：

```text
pages/WorkbenchPage.tsx
components/workbench/
  TopBar.tsx
  LeftRail.tsx
  SessionRow.tsx
  ReportVersionList.tsx
  ConversationStream.tsx
  RequirementCard.tsx
  RequirementField.tsx
  GenerationProgress.tsx
  RightRail.tsx
  RuntimeTrace.tsx
  WorkbenchComposer.tsx
  ReportPaper.tsx
styles/workbench.module.css
```

- `AppShell/Navbar` 改为原型顶部：工作台、模板中心、连接状态、用户。保留 `/history` 兼容重定向到工作台并选中对应会话。
- `LeftRail` 使用新 sessions API，按今天/过去 7 天/更早分组；仅 active session 展开最多 3 个版本。
- 中央不再在 Chat/Running/Report 三个页面间切换；连续渲染消息、RequirementCard、进度和选中版本报告。
- `RightRail` 根据 phase 渲染完整度、待补充字段、预计模块、业务进度或后续建议；Runtime 默认折叠。
- `ReportPaper` 复用 `adapter/reportAdapter.ts`、`ReportRenderer.tsx`、registry 和 blocks，不复制图表/表格逻辑。
- `RunningView.tsx` 退役；`ChatCards.tsx` 保留 legacy card 渲染一个兼容周期。
- `StandaloneReportPage` 暂时保留，但移入 AuthGuard；后续分享功能再设计，不用 URL 承载 blocks。

视觉以已确认 HTML 为基准，使用 CSS Modules 和 `antdTheme.ts` 中的 token；不在新组件继续堆大量 inline style。

## 8. Full application visual rebuild

现有 React 页面不作为视觉基础。本次以 `docs/intelligent-analysis-workbench.html` 为唯一设计基准，从零重建登录、工作台、模板中心和安全报告页。Ant Design 继续用于无障碍与交互基础（Form、Input、Select、Dropdown、Modal、Tooltip、Table、Skeleton、Message），但不使用其默认后台布局或默认 Card/Menu 外观。

### Visual foundation

- 将原型的暖灰画布、纸张白、深海军蓝、青绿色、琥珀异常色、边框、阴影、圆角和字体层级转成 CSS variables 与 Ant Design `ConfigProvider` tokens。
- 新建 `frontend/src/styles/tokens.css`、`global.css` 与页面 CSS Modules；新工作台组件禁止继续堆大量 inline style。
- 标题采用中文友好的宋体显示栈，数据与操作使用清晰无衬线栈；图表色板与状态色统一。
- Ant Design 的 Form/Input/Table 等通过 component tokens 与局部 class 重塑，避免默认 Ant Design 后台感。

### Ant Design theming acceptance

所有实际使用的 Ant Design 控件都必须通过原型同源 token 和局部样式统一，不能只改 `colorPrimary`：

- Button/Input/Select/Dropdown/Modal/Tooltip/Table/Tabs/Message/Skeleton/Form；
- default/hover/active/focus/disabled/loading/error 全状态；
- Select/Dropdown/Date 类弹层、Modal、Message 等 portal 内容；
- 表格表头、行高、数值对齐、悬停和分页；
- 登录与模板表单的 label、校验信息和输入状态。

建立一页仅开发环境使用的 UI gallery（或对应 Storybook-free test route），集中展示这些控件的所有状态，用截图回归确认不存在默认 Ant Design 风格泄漏。Ant Design 只提供交互、键盘与可访问性，最终视觉必须服从原型。

### Login page

登录页完全废弃当前深色 GitHub 风格，从零设计为与原型一致的编辑部式分析入口：

- 暖灰纸张背景与轻量网格纹理；
- 海军蓝品牌文字与青绿色主操作；
- 左右非对称构图：品牌价值/分析过程视觉 + 克制的登录表单；
- 复用相同 Logo、字体、按钮、输入框和连接状态语言；
- 桌面双栏，窄屏收敛为单列表单。

### Workbench

按已确认原型完全重建顶部和三栏，不复用 `ChatPage/ChatView/RunningView/ReportView/Navbar/AgentTimeline` 的现有 DOM 与样式，只迁移必要逻辑。中央连续渲染消息、RequirementCard、进度和 ReportPaper。

### Template center

模板中心改成“需求模板资产库”，而不是默认 Ant Design 三列 Card 网格：

- 左侧分类、搜索和排序；
- 中央模板列表/画廊，展示目标、指标、时间/范围占位和预计报告模块；
- 右侧或抽屉预览完整 RequirementCard；
- 使用模板后新建会话并进入待确认状态。

### History and report pages

- 移除独立历史主页面，`/history` 兼容跳转到工作台并聚焦左侧历史会话；历史和版本由 LeftRail 统一管理。
- 保留安全的 `/report/:sessionId/:version`，放入 AuthGuard；从后端按 session/version 读取，不再把 blocks 编进 URL。
- 安全报告页复用 ReportPaper/ReportRenderer，并提供全屏查看、打印和导出；不引入公开分享 token。

### Component migration

新增/重组：

```text
pages/
  LoginPage.tsx                 # 完全重写
  WorkbenchPage.tsx
  TemplateLibraryPage.tsx
  SecureReportPage.tsx
components/workbench/
  TopBar.tsx
  LeftRail.tsx
  SessionRow.tsx
  ReportVersionList.tsx
  ConversationStream.tsx
  RequirementCard.tsx
  RequirementField.tsx
  GenerationProgress.tsx
  RightRail.tsx
  RuntimeTrace.tsx
  WorkbenchComposer.tsx
  ReportPaper.tsx
components/templates/
  TemplateFilters.tsx
  TemplateList.tsx
  TemplatePreview.tsx
styles/
  tokens.css
  global.css
  *.module.css
```

旧组件在新页面全部接通并验证后删除或标记 legacy；不要在同一提交里先删除再重建。

## 9. Template center migration

将 `TemplateCenter.tsx` 从 localStorage 改为 `/api/v1/templates`：

- 保存模板时取当前已确认 RequirementCard，去除 session-specific 字段（ID/version/confirmed_at），保存配置。
- 使用模板时新建 session，后端返回预填的 `complete` RequirementCard；用户仍需确认后生成。
- 支持列表、创建、重命名/描述、删除；按当前用户隔离。
- 迁移期间读取旧 `ragent_templates` 一次，允许用户手动导入或忽略；不要静默把旧 blocks 当需求配置。

## Implementation Sequence

## Phase 0 — Add test infrastructure

- Backend：增加 `pytest`, `pytest-asyncio`, `httpx` 的 dev requirements 与 `backend/tests/conftest.py`。
- Frontend：增加 Vitest、Testing Library、jsdom 和 `vitest.config.ts`。
- 添加最小 smoke tests，确保基础设施红/绿可验证。
- 不改运行时行为。

验证：

- `pytest backend/tests -q`
- `cd frontend && npm test -- --run`
- `cd frontend && npm run build && npm run lint`

## Phase 1 — Shared contracts and pure reducers

1. 后端新增 RequirementCard Pydantic 模型及验证测试。
2. 前端新增 TS 镜像、AnalysisPhase、ReportVersion、SessionSummary。
3. 新增纯 `analysisReducer`，覆盖合法 phase、requirement replace、version append/select、error。
4. 扩展 SSE parser types，但暂不切换 UI。

关键测试：complete 必须 `missing_fields=[]`；locked 必须有 confirmed_at；重复 report version 不得 append；版本切换不修改 busy/phase。

## Phase 2 — Persistence repositories and migrations

1. 更新 `init_pg.sql` 创建 requirement/report/template 表。
2. 新增 analysis repository：create/update/confirm requirement、append/get report versions、session snapshot。
3. 扩展 conversation repository 支持 metadata，并在同一事务中写指针消息。
4. 新增 template repository 与 CRUD。
5. 修复 session user_id bug。

测试使用事务隔离的 PostgreSQL 或 repository fake；验证并发 version unique、用户隔离、重启后 snapshot 可恢复。

## Phase 3 — Requirement-analysis backend flow

1. 把当前 `_intent_analyze` 与 `ClarifyDecision` 的职责抽到 requirement parser。
2. 新增 `requirement_options.py`，集中生成受控 options/assumptions。
3. 构建只允许 Schema tools 的 requirement-analysis graph。
4. `/chat mode=new|supplement` 发送 `phase → thinking/trace → requirement → done`，并持久化。
5. 结构性测试确认 `validate_sql/execute_sql/report_agent` 在此流程调用次数为 0。

保留 legacy graph route，但新 API 模式不再依赖 `chosen_tool`。

## Phase 4 — Confirmed execution and report versions

1. 实现 PATCH requirement 与服务端重算状态。
2. 实现 `/confirm`：事务锁定 requirement，发 `phase=generating`，运行现有 Data/SQL/Report 子图。
3. 成功事务写 report v1、conversation pointer 与 session phase；SSE report 含版本信息。
4. 实现 adjust：加载 base version，执行新上下文，成功写 v2/v3。
5. 实现 retry，保留 failure action。
6. 实现 sessions/snapshot/report GET APIs。

核心测试：确认前 SQL spy=0；不完整 confirm=409；成功次数等于版本数；失败不新增版本；select/fetch version 不调用 LLM。

## Phase 5 — Visual foundation and frontend state migration

1. 将原型色板、字体、阴影、边框、密度和响应式断点转换为 `tokens.css/global.css/antdTheme.ts`；加入所有 Ant Design 控件的 component token 与 portal 样式。
2. 建立开发用 UI gallery 或测试路由，覆盖 Button/Input/Select/Dropdown/Modal/Tooltip/Table/Tabs/Message/Skeleton/Form 的所有状态并做截图基线。
3. 在旧页面背后接入 analysisStore/reducer 和 SSE v2。
4. API client 增加 chat modes、PATCH requirement、confirm/retry、sessions snapshot、report fetch。
5. 暂时保留旧 store/legacy card，建立兼容 adapter。
6. 修复 session 切换时 reports 泄漏、TTL 未更新、finally 重置 phase 等现有 bug。

验证 reducer 单测、UI gallery 截图和当前 API 回归，再进入页面重建。

## Phase 6 — Full application page rebuild

按原型从零重建，不在旧 DOM 上渐进改样式：

1. 完全重写 LoginPage 为暖灰纸张/海军蓝/青绿的编辑部式入口。
2. 重建 Workbench TopBar 与三栏骨架；移除数据域/目录概念。
3. 左侧 sessions 分组、状态和仅当前会话版本展开。
4. 中央连续 ConversationStream、RequirementCard missing/complete/locked 三态。
5. GenerationProgress 和动态 RightRail/Runtime。
6. ReportPaper 组合现有 ReportRenderer；收藏、导出、聚焦和继续调整。
7. 重建 TemplateLibraryPage 和模板预览/编辑表单。
8. 重建 SecureReportPage，并纳入 AuthGuard。
9. `/history` 兼容跳转到工作台历史位置。
10. 响应式：1440 三栏；1180 收起右栏；880 收起两侧并保留显式入口。

使用组件测试和浏览器截图验证按钮、表单、弹层、需求补丁、确认、版本选择和 phase 文案；不得出现默认 Ant Design 风格泄漏。

## Phase 7 — Template center backend migration

1. 模板 API/repository 接入前端 store。
2. 保存当前 confirmed RequirementCard。
3. 使用模板创建预填 RequirementCard，仍需确认。
4. 提供旧 localStorage 模板迁移提示。

## Phase 8 — Compatibility cleanup and documentation

- `ChatCards` 仅保留 legacy read-only renderer；确认所有活跃客户端升级后再删除 intent/options/confirm/preview types。
- 退役 RunningView 和双 viewMode/intentPhase 状态。
- `/history` 重定向到工作台；StandaloneReportPage 纳入 AuthGuard。
- 更新 README、CLAUDE.md、SSE/API 文档和数据库初始化说明。

## Code Quality Guardrails

- Python 延续仓库惯例：`from __future__ import annotations`、完整函数签名、`str | None` 联合类型、Pydantic v2、明确异常类型、日志使用参数化格式。
- 所有 PostgreSQL 查询使用 asyncpg 参数绑定；requirement/report/message/session 状态写入必须在同一 transaction 中。
- `main.py` 只负责 HTTP/SSE 编排；Requirement parser、options provider、repositories 和 graph node 分离，禁止继续形成入口巨石。
- 所有 AnalysisPhase 合法转移集中在后端状态机和前端 reducer 中；React 组件不得直接随意写 phase。
- session/report/template 的 read/write 必须同时约束 JWT `user_id` 与 `session_id`，避免跨用户访问。
- Pydantic 与 TypeScript RequirementCard 镜像必须有字段对照测试；新增枚举时两侧同一提交更新。
- TypeScript 保持严格类型；新契约使用判别联合，避免继续扩散 `Record<string, unknown>`、双重 `as unknown as` 和猜测式字段读取。
- 新 store/reducer 保持纯函数和不可变更新；SSE 网络副作用留在 action/service 层。
- CSS 统一使用语义 token 与 CSS Modules；新页面禁止大段 inline style、魔法颜色和无作用域全局覆盖。
- Ant Design portal 组件优先通过 `ConfigProvider` component token、`classNames`/`styles` API 定制；避免散落 `!important` 或依赖不稳定内部 DOM。
- 旧 API/card/store 先通过明确 adapter 兼容，再按阶段删除；避免一次性删除全部旧页面产生无法审查的大 diff。
- 每一阶段完成静态 diff review：契约、状态转移、权限、事务、错误恢复、响应式与视觉 token 分别检查。

## Verification Strategy for an Environment-Limited Machine

本机只执行不依赖项目运行环境的静态验证：

- 检查 Pydantic/TypeScript 手工镜像字段名称、可空性、枚举和版本语义；
- 检查所有 phase 的合法入口、出口、错误与重试路径；
- 审查 requirement-analysis graph 的可用工具集合，确认 SQL/Report tools 不可达；
- 审查 `/confirm` 的权限、完整性校验、事务锁与版本写入顺序；
- 审查不同用户的 session/report/template 查询均带 `user_id` 条件；
- 审查报告版本 append-only，失败路径不写新版本；
- 审查 reducer 中 `phase/requirement/version/error` 为单一事实来源；
- 使用 TypeScript/Python 语法与 lint 规则进行源码级检查（仅当相应工具已存在，不安装环境）；
- 使用 `git diff --check` 和人工 diff review；
- UI 改造以原型截图、token 对照表和组件状态矩阵做静态审查。

以下命令和 E2E 场景是交付给具备 Python、Node 和 PostgreSQL 环境的机器执行的验收清单；在本机未执行时，结果必须明确标记为“未验证”。

## Environment-dependent automated checks

Backend：

- vague question → `parsing → awaiting_missing`，SQL/Report spies 为 0；
- clear question → `parsing → awaiting_confirm`，SQL spy 为 0；
- PATCH options → complete requirement；
- incomplete confirm → 409；
- confirm → `generating → report_ready` + v1；
- adjustment → v2 with parent_version=1；
- report fetch/select path 无 LLM/graph 调用；
- SQL failure → error，retry 后成功且只新增一个版本；
- 不同用户无法读取会话/模板；
- 重新创建 app/agent 后从 PG snapshot 恢复 phase、draft、versions。

Frontend：

- reducer phase transitions；
- RequirementCard missing options、complete confirm、locked state；
- confirm 前不调用 confirm API；
- 版本切换只 fetch/cache，不开 SSE；
- session 切换只展开 active versions；
- Runtime 默认折叠；
- template save/use 仍进入 awaiting_confirm；
- UI gallery 中所有 Ant Design 控件的 default/hover/focus/disabled/loading/error 与 portal 弹层符合原型 token；
- 登录、工作台、模板中心和安全报告页截图在桌面/平板宽度下无视觉割裂或默认 Ant Design 泄漏。

## Environment-dependent manual E2E

1. 登录并新建宽泛问题“帮我分析一下销量”。
2. 验证只出现需求解析，不出现报告或 SQL trace。
3. 补充“2024 全年、华东区域”，接受假设，确认按钮启用。
4. 点击确认，观察 Data/SQL/Report 进度并得到 v1。
5. 输入“增加华南区域对比”，得到 v2，v1 可直接回看。
6. 切换历史会话，只有 active 会话展开版本。
7. 重启 backend，重新登录/打开会话，确认待确认状态或报告版本可恢复。
8. 模拟 SQL 错误，验证原会话重试。
9. 保存需求为模板，用模板创建新会话并再次确认。
10. 在 1440×1000、1180 和 900 宽度检查布局；浏览器控制台无异常。

## Critical Files

Backend：

- `backend/app/main.py`
- `backend/app/agent/parent_graph.py`
- `backend/app/agent/sql_graph.py`
- `backend/app/models/contracts.py`
- `backend/app/tools/registry.py`
- `backend/app/infra/conversation/repository.py`
- `backend/app/infra/checkpoint/session.py`
- `backend/scripts/init_pg.sql`

Frontend：

- `frontend/src/App.tsx`
- `frontend/src/stores/session.ts`
- `frontend/src/api/chat.ts`
- `frontend/src/api/api.ts`
- `frontend/src/types/report.ts`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/components/chat/ChatCards.tsx`
- `frontend/src/components/report/ReportRenderer.tsx`
- `frontend/src/pages/TemplateCenter.tsx`
- `frontend/src/theme/antdTheme.ts`

## 10. Current execution boundary

仓库关键 Python 文件为 TSD 加密 blob（`%TSD-Header-###%`），磁盘上看到的不是真实源码；明文必须从 `git show HEAD:<path>` 读取。当前本机会话内不修改任何 TSD 加密文件，避免双写或覆盖。计划因此分两条推进路径：

### 10.1 Can be implemented in this session

不依赖加密源码的代码、文档、契约、设计：

- 后端新增独立模块（不修改 `backend/app/main.py` / `parent_graph.py` / `sql_graph.py` / `infra/*` 等加密文件）：
  - `backend/app/models/requirement.py`（已有契约骨架）。
  - `backend/app/agent/requirement_parser.py`（LLM 结构化生成 RequirementCard 的独立函数库，可被未来 requirement-analysis graph 复用）。
  - `backend/app/agent/requirement_options.py`（时间、区域、指标等候选生成器，集中受控业务语义）。
  - `backend/app/services/requirement_service.py`（PATCH / confirm / adjust / retry 业务层，独立于 FastAPI 入口与 LangGraph，便于独立静态审查与测试时 mock 替换）。
  - `backend/app/services/report_version_service.py`（append-only 版本写入 + 事务；暴露与 `agent.requirement_draft`、`app.conversations` 同一事务的快照写方法）。
  - `backend/app/services/template_service.py`（用户隔离的模板 CRUD）。
  - `backend/app/api/schemas.py`（API Pydantic 契约：`ChatRequest`、`PatchRequirementRequest`、`ReportVersionSummary` 等）。
- SQL DDL 文档：`docs/persistence.md`（含完整 `CREATE TABLE` 与触发器/索引，作为合并到 `init_pg.sql` 的源材料）。
- 契约对照文档：`docs/contracts/requirement-card.md`（Pydantic ↔ TS 字段名、可空性、枚举与版本语义对照表）。
- 状态机文档：`docs/state-machine.md`（HTTP `phase` 与 LangGraph `agent_state` 双层关系、合法的入口/出口/错误路径）。
- API 行为文档：`docs/api/chat.md`（路径、模式、payload 字段、错误码、权限约束）。
- SSE 协议文档：`docs/sse-v2.md`（事件、payload schema、向后兼容策略）。
- 前端全部改造（前端源码未加密）：
  - `tokens.css / global.css / *.module.css` 视觉系统；
  - `ConfigProvider` component tokens；
  - `analysisStore` / `analysisReducer` / SSE adapter / REST client；
  - `WorkbenchPage` / `LoginPage` / `TemplateLibraryPage` / `SecureReportPage`；
  - 全套工作台组件（`TopBar / LeftRail / SessionRow / ConversationStream / RequirementCard / GenerationProgress / RightRail / RuntimeTrace / WorkbenchComposer / ReportPaper` 等）；
  - Ant Design 控件主题化与 UI gallery；
  - 旧页面（`ChatPage / ChatView / RunningView / ReportView / ChatCards / Navbar / HistoryPage`）通过 adapter 过渡，最终标记 legacy 或删除。
- 设计/原型 HTML：`docs/intelligent-analysis-workbench.html`（已有）。

### 10.2 Requires TSD decryption to be applied

只能在明文后端源文件中实现的工作。本会话给出**占位 + 行为规约 + 详细注释**，并在 plan 中记录接入步骤：

- `backend/app/main.py`：
  - 新增 `PATCH /api/v1/sessions/{session_id}/requirement`、`POST /api/v1/sessions/{session_id}/confirm`、`POST /api/v1/sessions/{session_id}/retry`；
  - `POST /api/v1/chat` 改造为支持 `mode: new | supplement | adjust`，移除 `chosen_tool` 字段；
  - SSE 事件扩展：`phase / requirement / report / done`；
  - 移除 `request.session_id` 被当 `user_id` 写入 session 表的 bug；
  - `lifespan` 不再 `MemorySaver` 重建时静默丢失 session。
- `backend/app/agent/parent_graph.py`：
  - 收敛为两个入口（requirement-analysis 与 confirmed-execution）；
  - 父图节点重排：security_guard → classify_intent → data_agent(schema only) → requirement_parse → END；
  - confirmed-execution：load_confirmed_requirement → data_agent(refresh) → sql_agent → evaluate/retry → report_agent → persist_report；
  - 在 sql_agent 入口加硬 gate：`draft.user_id == jwt.user_id AND draft.status == 'confirmed' AND draft.missing_fields == []`。
- `backend/app/agent/sql_graph.py`：
  - 移除 `_intent_analyze` 单独分支和 `chosen_tool` 读路径；
  - 接收 `requirement_draft` 直接进入 plan → generate → validate → execute → evaluate；
  - `ClarifyDecision` 保留作为内部维度决策，不向 SSE 暴露。
- `backend/app/infra/checkpoint/session.py`：
  - 修复 `create_session` user_id；
  - 增加 `latest_requirement_draft_id / latest_report_version` 字段。
- `backend/app/infra/conversation/repository.py`、`backend/app/infra/db/postgres.py`：
  - 增加事务包裹 requirement/report/message/session 写入；
  - 新增读取 API（按 `(user_id, session_id)` 隔离）。
- `backend/scripts/init_pg.sql`：
  - 合并三张新表与索引（与 `docs/persistence.md` 同步）。

### 10.3 Working branch and review policy

- 本会话所有改动落在 `feat/conversational-workbench` 分支，不动 `master`。
- 每一批以“单一职责 + 静态可审查”为原则，commit 粒度不跨域：
  - 后端契约/服务/独立模块一提交；
  - 前端类型/状态/页面/CSS 一提交；
  - 文档（contract/state-machine/api/sse）一提交。
- 每一批提交后做静态 diff review：契约一致性、状态转移、权限、事务、错误恢复、视觉 token、Ant Design 风格泄漏检查。
- 不在本机声称“测试通过”；未运行验证一律标注“未验证”。

### 10.4 Frontend-first delivery plan (this session only)

按最小依赖顺序产出，每一步都可不依赖环境运行：

1. **视觉基础**：`tokens.css` + `global.css` + `ConfigProvider` 主题化（与原型对齐）。
2. **类型与状态**：`types/requirement.ts`、`types/analysis.ts`、`types/report.ts` 扩展 `SSEEventType`。
3. **API/SSE 适配**：`api/analysisEvents.ts`、`api/analysisClient.ts`、`api/templatesClient.ts`、`api/sessionsClient.ts`。
4. **状态与持久化**：`stores/analysisStore.ts`（基于 `analysisReducer`）、`stores/templateStore.ts`、`stores/authStore.ts`（保留）。
5. **UI gallery 路由**：`/dev/ui-gallery` 集中展示 Ant Design 主题化控件。
6. **LoginPage 重建**。
7. **WorkbenchPage 重建**：TopBar + LeftRail + 连续对话中央 + 右侧分析助手。
8. **RequirementCard 组件**：三态（missing / complete / locked）、补丁选项、确认/修改按钮。
9. **GenerationProgress + RuntimeTrace**。
10. **ReportPaper**：复用现有 `ReportRenderer` 与 `registry`，结合 `ReportVersion`。
11. **TemplateLibraryPage** + 模板预览/编辑。
12. **SecureReportPage**：从 `/report/:sessionId/:version` 读 PostgreSQL 报告版本。
13. **App.tsx 路由清理**：保留 `/`、`/templates`、`/report/:sessionId/:version`，将 `/history` 重定向到 `/`，新加 `/dev/ui-gallery`。
14. **遗留代码处理**：旧 `ChatPage/ChatView/RunningView/ReportView/HistoryPage/ChatCards/Navbar` 通过 adapter 过渡或标记 legacy；不一次性删除。

### 10.5 Acceptance contract (environment-dependent, must be run by user)

不在本机执行；本机仅提供静态 diff review。验收清单在具备 Python/Node/PostgreSQL 环境的机器上：

- `pytest backend/tests -q`
- `cd frontend && npm test -- --run`
- `cd frontend && npm run build && npm run lint`
- `python -m http.server` 打开 `docs/intelligent-analysis-workbench.html` 走查交互
- 端到端流程：登录 → 提问 → 需求解析 → 确认 → v1 → 调整 → v2 → 版本回看 → 模板保存/复用 → 重启恢复

### 10.6 How to resume after TSD decryption

如果你在后续解锁 TSD 加密（明文同步到磁盘）：

1. `git checkout master && git merge feat/conversational-workbench`，将本会话所有可独立运行的前端与文档合入。
2. 按本计划第 10.2 节逐文件实施明文后端接入。
3. 接入过程中优先复用本会话已交付的 `requirement_parser / requirement_options / requirement_service / report_version_service / template_service`；它们与 LangGraph 节点解耦，可在主流程中通过 `await asyncio.to_thread(...)` 或 `await anyio.to_thread.run_sync(...)` 包装同步代码。
4. 每接入一个节点，单独提交；不允许“主流程一次大改”。
5. 接入完成后用第 10.5 节清单进行端到端验收。


