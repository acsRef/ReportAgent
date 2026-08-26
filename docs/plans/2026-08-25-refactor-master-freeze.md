# ReportAgent 重构冻结基线（Master Baseline）

> 状态: 进行中

## Context

ReportAgent 经过多轮迭代后功能持续增加：两图链路（requirement_analysis_graph → confirmed_execution_graph）、三态落库、后台执行（ExecutionRegistry）、四层记忆（L1/L2/L2.5/L3）、MCP FAQ client、llm_resilience、PG observability 都已在位。但架构层面积累了三类问题：

1. **边界分叉风险**：`tools/data_tools.py` 与 `rag_schema.py` 仍保留本地 schema 匹配实现，与 MCP 路径形成两套 Retrieval；RAG 能力（embedding/rerank）在 ReportAgent 内有重复实现倾向。
2. **可靠性散装**：超时/重试逻辑分散在 `llm_resilience.py`、sql_graph、main.py SSE 各处，没有统一的 ErrorEnvelope 与 Recoverability 分类；前端断连、后台任务超时、MCP timeout 各说各话。
3. **验证体系缺失**：没有 Golden Set、没有行为级 Evaluation、没有 Playwright E2E、Langfuse 未接入，导致「每次优化难以单独证明有效」。

本 plan 是前几轮讨论（15-Phase 重构总 Plan → 风险审查 → 19 条修订决议 → V2 完整版对照吸收）的**最终收敛**，作为后续所有 Phase 实施的宪法。2026-08-25 吸收 V2 完整版增量：每 Phase 目标/验收清单、Agent≠Workflow 原则、目标目录冻结（§二·二）、Definition of Done 总清单（附录 B）、12 个面试问题（附录 C）、ADR 与 Forbidden Patterns（§十八）、ragent-py 模型配置快照（附录 D）。目标不是重写项目，而是：

> 在保留现有能力的基础上，清理历史架构、明确边界、建立真正的 Agentic Loop、把 Memory 和 Context 做成可控系统，并把前端、报表、MCP、Timeout、Langfuse、Evaluation 全部接成闭环。

项目性质约束：这是**个人面试项目**，目标是展示 Agent Engineering 能力，不是生产商用平台。因此一切「生产系统还可以做 X」的想法都要过一遍本 plan 的「明确不做」清单。

核心设计原则（冻结）：

```text
Agentic where uncertainty exists.
Deterministic where correctness matters.
```

---

## 设计

> **Plan-of-plans 说明**：本文件是伞形基线，冻结架构契约与阶段门。每个 Phase 启动时另开独立的 `YYYY-MM-DD-<phase-slug>.md` 实施 plan（含 TDD 任务分解），在本索引登记并回链本文件。这符合「one topic per file」纪律——本文件不承载逐文件改动细节。

### 一、最终定位

**ReportAgent = Stateful Agentic Data Analysis Workbench**

```text
自然语言 → 需求理解 → 需求补全/确认 → Schema/Knowledge Retrieval
→ Query Planning → SQL Generation → SQL Validation → SQL Execution
→ Execution Feedback / Repair → Report Planning → Report Version
→ Frontend Workbench
```

| Agent 负责 | Deterministic Runtime 负责 |
|---|---|
| 理解、决策、Tool Selection、Planning、Clarification、Diagnosis、Repair、Report Planning | Security、Authorization、SQL Parsing/Safety、EXPLAIN、DB Execution、Persistence、Row Limit、Report Rendering |

**核心架构原则：Agent ≠ Workflow**。Workflow 负责确定性流程、状态传递、生命周期、边界、错误传播；Agent 负责理解、决策、Tool Selection、Planning、Repair：

```text
Requirement Workflow：生命周期 / 边界 / 错误传播
  └── Requirement Agent：是否澄清？是否调 Tool？产出 RequirementCard

Execution Workflow：Plan → Generate → Validate → Execute → Evaluate（确定性骨架）
  └── Execution Agent：retrieve? generate? execute? repair? finish?（动态决策）
```

骨架是 Workflow，岔路口的决策是 Agent——以此避免「整个系统其实只是一个固定 DAG，不算 Agent」的质疑。

### 二、系统边界（强制）

- **RAG 项目（acsRef/Rag）**：Document Ingestion → Parsing → Chunking → Embedding → Retrieval → Reranking → Evaluation → **MCP Server**。定位 Retrieval Runtime。
- **ReportAgent**：Frontend Workbench + Agent Runtime + Memory + SQL Runtime + Report Runtime + SSE + Checkpoint + Observability + Evaluation。定位 Agent/Application Runtime。
- ReportAgent **不得重新实现**：Embedding、Chunking、Vector Search、Reranking、RAG Indexing、RAG MCP Server。只通过 `MCP Client → RAG MCP Server` 使用。
- 推荐 MCP 工具面：`search_schema` / `get_schema` / `search_knowledge`（不暴露 embedding/vector_search/chunk/rerank 这类内部机制）。

### 二·二、目标目录结构（Repository Boundary，2026-08-25 冻结）

`backend/app/` 最终目录冻结如下，是后续所有文件迁移的**唯一依据**：

```text
backend/app/
├── agents/                # 谁负责思考
│   ├── requirement/       # graph.py / nodes.py / prompts.py / state.py
│   ├── execution/         # graph.py / nodes.py / prompts.py / planner.py / repair.py / state.py
│   ├── report/            # graph.py / nodes.py / prompts.py / state.py
│   └── shared/
├── context/               # Agent 当前看到什么：runtime.py / decision.py / policy.py / assembler.py
├── memory/                # 长期保存什么：conversation.py / semantic.py / query.py / policy.py / manager.py
├── reliability/           # 出错怎么办：timeout.py / retry.py / backoff.py / errors.py
├── llm/                   # 用什么模型思考：client.py / config.py / normalization.py / structured_output.py / errors.py
├── mcp/                   # 怎么访问 RAG：client.py / contracts.py / telemetry.py
├── tools/                 # Agent 有什么能力：registry.py / contracts.py / definitions/
├── report/                # 怎么定义/校验报告：spec.py / validator.py / versioning.py
├── observability/         # 怎么看发生了什么：tracer.py / langfuse.py / redaction.py
├── evaluation/            # 怎么证明变好了：requirement/ memory/ retrieval/ sql/ repair/ report/ e2e/
├── api/                   # HTTP/SSE 边界
├── services/              # 只放跨域应用服务（克制：能归属明确 bounded context 的一律不放这里）
├── models/                # 共享持久化/领域模型
└── legacy/                # 废弃代码，新代码 MUST NOT import：agents/ tools/ adapters/
```

边界要点：

- `agents/report/` = **Agent 怎么决定报告结构**；`report/` = **报告这个 Domain Object 怎么定义、校验、版本化**。两个边界不合并。
- `tools/` = 能力抽象（Tool 是 Agent 看到的契约），`mcp/` = 传输/协议边界（Tool 的实现可落在 MCP client）——不把 `mcp/` 做成 `tools/` 子目录。
- **不建 `runtime/` 大聚合层**：context / memory / reliability 职责完全不同（看到什么 / 保存什么 / 出错怎么办），顶层分列；`runtime/` 极易退化成垃圾桶（session/helper/manager/utils 全塞进去），半年后重回混乱。
- `legacy/` 只留顶层一份，不同时设 `agents/legacy/`。
- **硬规则（P1 写入宪法版 CLAUDE.md）**：禁止新建 `utils2/` / `managers/` / `runtime/` / `helpers/` / `common2/` 类 generic 文件夹；代码放最窄的既有域边界。

### 三、Canonical Flow（唯一主链路）

```text
User → Security Guard → Context Runtime → Requirement Agent (MCP/RAG)
  → RequirementCard → 缺失? Clarify : User Confirm
  → Execution Agent [Context Runtime + MCP/RAG]
      Plan → Generate → Validate → Execute → Evaluate
        SUCCESS ↓            FAILED → Diagnose → Repair → Regenerate(有限次)
  → Report Agent → ReportSpec → ReportVersion → REST/SSE → Frontend
  → Langfuse + Eval
```

### 四、Agent 职责（三个核心智能阶段）

**Requirement Agent**：理解意图、识别 metric/dimension/filter/time_range、决定是否 Retrieval、决定调用哪个 MCP Tool、判断缺失信息、决定 Clarification、生成 RequirementCard。不做 SQL/执行/渲染/持久化。

**Execution Agent**（核心）：主循环 `Plan → Retrieve → Generate → Validate → Execute → Evaluate`。失败路径必须走 `Error Classification → Diagnose → Repair Strategy → Regenerate`，把 Original Requirement / Current Schema / Previous SQL / Failure Category / Error Message / Validation Result / Retry Count 传入 Repair——**禁止 blind retry**。上限：达到 `MAX_SQL_REPAIR_RETRIES` 后 Persist Failure ReportVersion → SSE error → 前端 Retry/Adjust，不无限循环。

**Report Agent**：`Execution Result → ReportSpec → ReportVersion → Frontend Renderer`。ReportSpec 至少支持 title/summary/insight/kpi/chart/table/section/recommendation/alert。数据真实性原则：所有数值、排名、统计、图表数据必须来自实际 Query Result，禁止编造数字/指标/字段/修改 SQL 结果。

### 五、State Contract

当前巨大 State 拆为五块：

```text
RequestState     request_id, session_id, user_id, original_query(immutable), current_query
RequirementState normalized_query, schema_candidates, requirement_card, missing_dimensions,
                 clarification_history, confirmation_status
ExecutionState   confirmed_requirement, schema_context, query_plan, generated_sql,
                 validation_result, query_result, execution_status, error, retry_count
ReportState      report_spec, report_version, chart_config, insight
RuntimeState     trace_id, active_agent, memory_context, tool_calls, mcp_calls
```

### 六、Memory & Context Runtime

四类记忆，职责分离：

| 类型 | 职责 | 关键规则 |
|---|---|---|
| **Session State** | 当前分析正在发生什么（year=2024、region=华东…） | 当前 session 有效、用户修改立即覆盖、不属于长期记忆 |
| **Conversation Memory** | 多轮连续性 | Recent Messages（最近 8~10 条）+ Summary（超过窗口后 Old Summary + New Batch → New Summary，**覆盖重写**）。现 `context.py` 的 L1/L2/L2.5 设计保留 |
| **Semantic Memory** | 跨 Session 长期语义 | `stable_preference` / `semantic_fact` / `temporary_preference` |
| **Query Memory** | 历史成功查询经验 | **Experience 不是 Truth**：始终 `Current Schema > Historical SQL` |

**读取时机**：Recall Before Agent —— `User Request → Security → Session State → Context Runtime → Memory Decision → Selective Recall → Context Assembly → Agent`。

**Selective Recall 触发条件**（默认不自动召回全部长期记忆）：① 历史引用（“继续/刚才那个/再按产品细分”）；② 长期偏好影响当前任务（报告生成时召回图表偏好）；③ 业务定义影响理解（GMV 定义）；④ Query Experience 与当前查询高相似。不召回：query 已完整、纯闲聊、与历史无关、用户明确覆盖过去状态。

**Agent-specific Policy**：

| Agent | Conversation | Semantic | Query |
|---|---|---|---|
| Requirement | ✅ | ✅ | 少量 |
| Execution | 少量 | ✅ 业务事实 | ✅ |
| Report | ✅ 少量 | ✅ Preference | ❌ |

**写入时机**：Write After Reliable Event —— `Task Outcome → Memory Write Decision → Insert/Update/Discard`。

**V1 简化（冻结，来自风险审查）**：
- **行为偏好自动 promotion 降级不做**。第一版只有 `Explicit user statement → stable_preference`（“以后都用柱状图”直接存）；「连续三次点击柱状图」这类行为证据**不自动**变成长期记忆。candidate/evidence_count/promotion 机制留到有时间再做。
- **Confidence 不让 LLM 自己拍**。规则固定：`explicit_user_statement → confidence = high`；`explicit_business_definition → confidence = high`；`LLM inferred preference → 不进入 active long-term memory`。先把 Memory 做成「可靠记忆」而不是「猜测记忆」，以此控制 memory pollution。
- **temporary_preference 绑定 session_id，任务结束可过期**；Session State 与 TemporaryPreference 逻辑上分开、存储上第一版统一放 `agent.session`（字段区分），等 Evaluation 证明需要独立生命周期再拆表。

**Lifecycle**：支持 INSERT / UPDATE / SUPERSEDE / EXPIRE / DELETE；状态 `candidate / active / superseded / expired`。新旧偏好冲突时旧 → superseded，避免多个 active 互相矛盾。`semantic_entry` 补字段：`scope(user|session) / confidence / status / source / session_id / expires_at / created_at / updated_at / last_accessed_at`。

**Conflict Priority（固定）**：Current User Requirement > Current DB Schema > Business Definition > Stable Preference > Query Experience > Conversation Summary。**Schema 永远不能被 Memory 覆盖**。

Context Runtime 新增统一入口：

```text
backend/app/context/runtime.py    # context_runtime.build(session_id, user_id, query, agent)
backend/app/context/policy.py     # agent-specific 召回策略
backend/app/context/decision.py   # 是否召回、召回什么
backend/app/context/assembler.py  # Filter → Conflict Resolution → Token Budget → Assembly
```

**Query Memory 写入门槛不变**：严格 `SQL Validation SUCCESS AND Execution SUCCESS` 之后；失败 SQL 只记录 `failure_category / failed_sql / retry_count / error`（沿用 `QueryMemory.record_failure()`），不进入成功 Query Memory。

### 七、Reliability Layer（独立横切层，新增）

新增 `backend/app/reliability/`（顶层，见 §二·二；不建 runtime/ 聚合层）：

```text
timeout.py    # TimeoutPolicy：分层 LLM/MCP/DB/SSE/Frontend/Background Task
errors.py     # ErrorEnvelope：{code, kind, recoverable, failed_action, message}
retry.py      # RetryPolicy：固定预算
backoff.py    # 退避工具
```

**统一错误码表（ErrorEnvelope.code，最小集）**：`LLM_TIMEOUT` / `LLM_UNAVAILABLE` / `MCP_TIMEOUT` / `MCP_UNAVAILABLE` / `MCP_INVALID_RESPONSE` / `SQL_SYNTAX_ERROR` / `SQL_EXECUTION_ERROR` / `SQL_TIMEOUT` / `REPORT_VALIDATION_ERROR` / `INTERNAL_ERROR`。Retry 分流原则：Transient → retry；Permanent → 不 retry；Agent-recoverable → Agent repair（MCP timeout→retry；SQL 错列→Agent repair；无效用户请求→clarification；认证错→直接 fail）。

要点：
- **统一 Failure Pipeline**：`Error → Classify → Record Trace → Determine Recoverability → Retry/Resume/Fail → Persist State → User-visible Error`。Frontend 不自己猜错误含义。
- **Retry 固定预算（settings 配置，不做动态/按用户调整）**：`SQL repair: 2`、`MCP: 2`、`LLM transient error: 2`。adaptive policy 等 Evaluation 数据说话。
- **MCP Timeout 分层**：ReportAgent 只关心 MCP request timeout，不理解也不感知 RAG 内部 embedding/rerank 慢——这是 boundary 的意义。
- **MCP 失败不许默默返回空数组伪装“没结果”**：必须明确告知 unavailable/timeout，由上层决定 retry/clarify/fallback/fail。
- **DB Timeout ≠ SQL 错**：区分 Query Timeout / Connection Failure / Permission Failure / Object Not Found / Syntax Error，只有可恢复错误进入有限 retry；禁止同一 SQL 无限重跑。
- **SSE Disconnect ≠ Backend Failed**：断连后任务继续跑完并持久化，前端轮询通知——现 `WorkbenchPage` 后台任务 polling 机制保留。
- **Frontend Timeout**：UI 停止等待 + poll session，不直接取消后端（除非将来明确实现 Cancellation Contract）。
- **Background Task Timeout**：超过 `MAX_TASK_DURATION` → Task Timeout → Persist FAILED → ReportVersion(error) → Trace → 前端 error。不允许永远停在 generating。

### 八、Tool / MCP Contract

- Tool Metadata 在现有基础上统一：`name / purpose / when_to_use / when_not_to_use / input_schema / output_schema / preconditions / postconditions / failure_policy / side_effects / examples / risk_level / permission / source`。Tool Description 是 Agent Contract，不只是文档。**Prompt 写得再好，Tool description 模糊，Agent 仍然会乱调用**——P5 必须先于 P7 做。
- Description 正反例：

```text
❌ search_tables: Search tables.
✅ search_schema:
   Purpose: Find database tables/columns relevant to the user's analytical question.
   Use when: schema 未知；查询引用业务概念而非已知表；生成 SQL 前需要候选表。
   Do NOT use when: 所需 schema 已在 context；任务只是执行已有 SQL。
   Output: table_name / columns / relevance / metadata。
   Failure: EMPTY_RESULT / MCP_TIMEOUT / MCP_UNAVAILABLE。
```

- **Tool Selection Policy**：每个 Tool 必须能回答「什么时候调用 / 什么时候不调用 / 调用前需要什么 / 调用后得到什么」——答案写进 description，不靠模型猜。
- **迁移策略（Feature Flag 受控）**：迁移期间允许 `PHASE2_MCP_ONLY=true/false` + `MCP 失败 → Local fallback`，但 fallback 必须 contract/input/output/error semantics 四一致。**Phase 5 起停止本地 RAG fallback**，最终只留 `ReportAgent → MCP → RAG` 一条路，防止双 Retrieval 系统永久分叉。

### 九、Unified LLM Migration（P6 正式任务）

> 总 Plan 初稿的 P6 只有「Adapter 化」一句，不够。本节将其升级为正式的模型迁移 Phase：目标是把 ReportAgent 当前对话/Agent 模型统一迁移到 **RAG 项目当前使用的免费 reasoning-capable chat model**，统一 Provider / Model / Base URL / Authentication / Generation Config / Structured Output / Reasoning Normalization / Retry / Timeout 八件事。Conversation / Requirement / Execution / SQL Generation / Repair / Report 全部走同一模型；不再维护「普通对话模型 / SQL 模型 / 复杂推理模型 / Report 模型」多套并存（除非后续 Evaluation 证明多模型路由有明显收益）。

**第一步先锁契约再动代码（6.2）**：Phase 6 开工不从 ReportAgent 开始——先从 Rag 仓库确认 chat model name / provider / base URL / API protocol / temperature / max tokens / reasoning behavior / structured output behavior / timeout，落成第六份架构文档 `docs/architecture/llm-contract.md`（与 P1 五份并列）。以后「模型换了」只需重对这一份契约。

**Adapter 结构（6.3/6.5）**——Agent 只能依赖 Adapter（`generate(...)` / `generate_structured(schema=...)`），禁止直接调用 provider SDK：

```text
backend/app/llm/
├── config.py              # LLM_API_KEY / LLM_MODEL / LLM_BASE_URL / LLM_TIMEOUT /
│                          # LLM_MAX_RETRIES / LLM_TEMPERATURE / LLM_MAX_TOKENS
├── client.py              # generate() / generate_structured()
├── models.py
├── normalization.py       # <think> 剥离等 reasoning 归一化（strip_think 从 SQL Agent 迁入）
├── structured_output.py   # JSON parsing + schema validation + invalid handling + retry
├── retry.py               # 与 reliability/ 共享语义
└── errors.py              # LLM_TIMEOUT / LLM_ERROR → ErrorEnvelope
```

要点：

- **配置收敛（6.4）**：旧 `MINIMAX_API_KEY` / `MiniMax-M3` / 旧 Base URL 从 canonical configuration 移除，全部收敛为 `LLM_*` settings；Agent 代码零 provider 硬编码。**CLAUDE.md 增量更新在 P6 当天**（Configuration 节 env 表换 `LLM_*`），不等 P15；P1 宪法版只写「统一 reasoning model、provider 无关」，不出现具体 provider 名。
- **Reasoning Normalization 集中（6.5）**：`<think>...</think>` 剥离统一进 `normalization.py`，Requirement / SQL / Report 共用；任何 Agent 不再自带模型兼容逻辑。管线固定为 `Raw LLM Response → Reasoning Normalization → Structured/Text Output → Agent`。
- **Structured Output 统一验证面（6.6）**：RequirementCard / QueryPlan / SQL Output / Repair Decision / ReportSpec 五类输出全部走 `generate_structured()`（含 JSON 解析、schema 校验、非法输出处理、retry）；Agent 不自己解析 JSON、不自己处理 reasoning 残留。
- **Generation 参数统一（6.9）**：temperature / max_tokens / timeout / retry 定义全局默认值集中在 config；个别任务（如 SQL generation 低温度、Report 略高）只有经测试证明必要才显式覆盖，禁止每个 Agent 随手设参。
- **Tool Calling / MCP 兼容性专项验证（6.7）**：reasoning 模型下必须实测三条路径——Requirement（search_schema → get_schema）、Execution（requirement → schema tools → SQL）、Failure（MCP timeout → Agent 正确识别失败、**不伪造 retrieval result**），覆盖 Tool Selection / Tool Arguments / MCP Calls / Multi-step Tool Use。
- **Prompt Compatibility 不假设（6.8）**：迁移后不得默认原 Prompt 可用——对普通中文对话 / Requirement / Clarification / Tool Selection / Planning / SQL Generation / SQL Repair / Report 全链路重测，重点记录迁移前后输出结构正确率 / Tool Selection Accuracy / SQL Success Rate / Repair Success Rate / Report Quality / Latency，形成 Phase 6 baseline comparison。
- **Migration Golden Set 复用 P0 资产（6.11）**：直接用 `evaluation/baseline_cases.json`（P0 的 20~22 例，已覆盖闲聊 / 多轮上下文 / Clarification / Schema Retrieval / SQL / EMPTY / Report / Memory）跑 Before Model vs Unified Model 对比，**不重建数据集**。对比指标至少：Requirement Accuracy / Tool Selection Accuracy / SQL Execution Success / Repair Success / Report Quality / Latency。
- **Timeout / Retry 与 Reliability Layer 集成（6.10）**：`LLM Call → Timeout → RetryPolicy → Backoff → Still Failed → ErrorEnvelope`，错误形如 `{"code": "LLM_TIMEOUT", "kind": "timeout", "recoverable": true, "failed_action": "llm"}`；调用 span 记录 model / latency / retry_count / timeout / status（P13 Langfuse 接入后自动可见，Adapter 层保证字段齐全）。
- **配置命名冲突风险（已知，P6 必须处理）**：现 `llm_resilience.py` 已占用环境变量 `LLM_MAX_RETRIES`（=重试次数 5），本节 `llm/config.py` 拟用同名——配置收敛时统一语义并消歧（该名字保留给重试次数；总预算/超时另名），不允许同名双义并存。

**Phase 6 验收标准（6.12）**：ReportAgent 不再依赖旧 MiniMax 配置 ✅ 所有 Agent 走统一 Adapter ✅ Model/Provider/Base URL 集中配置 ✅ Reasoning normalization 集中 ✅ Structured Output 统一接口 ✅ 五类结构化输出稳定 ✅ MCP Tool Calling 正常 ✅ Timeout/Retry 接入统一机制 ✅ span 字段齐全 ✅ Migration Golden Set Before/After 通过 ✅。

### 十、Prompt Engineering（P7 完整版）

**分层（不要巨型 Prompt）**：每个 Agent 的 prompt 由六层组成——System Contract / Role / Task Contract / Tool Policy / Output Schema / Safety Policy；Dynamic Context 由 Context Runtime（§六）统一注入，prompt 不自拼上下文。

**分 Agent 职责与禁区**：

- Requirement Prompt：理解意图、检测歧义、决定是否澄清、产出 RequirementCard。**禁止生成 SQL**。
- Execution Prompt：Planning、Tool selection、SQL 生成、执行结果解读、Repair 决策。
- Report Prompt：输入 RequirementCard + QueryResult + Report constraints + Memory preference，输出 ReportSpec。

**Negative Instructions（每个 prompt 必备）**：Do NOT invent tables/columns；Do NOT fabricate query results；Do NOT assume unavailable schema；Do NOT call search_schema when schema is already known。

**Tool Policy（显式写进 prompt）**：schema 信息不足 → 调 search_schema；schema 已在 context → 不重复调用；SQL 执行失败 → 先读 error 再决定 repair 策略。

**Prompt Versioning**：每个 prompt 带 `name / version / purpose / input / output` 元数据（如 `execution_sql_v2` / `report_generation_v3`），version 进 Langfuse（P13），改动可追踪。

**Prompt Eval 闭环**：每次 prompt 变更必须走 `Golden Set baseline → 新 prompt → compare`；不接受「感觉这个 prompt 更好」。

**新增 Prompt Rule 前先四问**：该由代码解决？Tool Contract 解决？State Contract 解决？Validator 解决？——都否才加 prompt rule。不无限堆 prompt。

### 十一、SQL Validation（三层，保留现状语义）

Static（危险函数黑名单 + SELECT-only + AST `Select` 校验 + 表白名单）→ Schema（EXPLAIN 对真 schema）→ Runtime（PostgreSQL 执行）。Validation 失败必须把 rejected SQL + error message 回灌 regeneration prompt。

### 十二、Report Runtime 与 Fact Checker 分层

ReportSpec Validator 分三层（不做对最终 HTML 文本的正则数字审计——`12345 / 12,345 / 1.23万 / 12.35%` 会大量误报）：

1. **结构校验**：chart field 来自 query result、table column 存在、KPI field 存在。
2. **数值校验**：结构化 ReportSpec 中数值必须来源于 Query Result 或明确允许的 aggregation/arithmetic。
3. **禁止自由生成**：Report Agent 输出的是 ReportSpec 而非自由 Markdown，校验对象是 `ReportSpec → QueryResult` 的映射，不是渲染产物。

### 十三、Observability（PG Trace + Langfuse + 统一 Redaction）

架构：`Agent Runtime → Observability Adapter → { PostgreSQL Trace, Langfuse }`。

Trace 树：Request → Security → Context(Memory) → Requirement Agent(LLM, MCP) → Execution Agent(LLM, MCP, SQL, Repair) → Report Agent(LLM) → Final Result。

**Span 字段明细（任何一次 Agent 执行都能从 Langfuse 重建）**：

- Trace Metadata：session_id / conversation_id / agent / model / prompt_version / status / latency / retry_count。
- LLM Generation：model / input / output / latency / tokens / status。
- Tool Span：tool_name / arguments / result / latency / status / error。
- MCP Span：server / tool / latency / timeout / status。
- SQL Span：sql / latency / rows / status / error / repair_attempt。

**PII Redaction 统一做在 Observability Adapter 之前一层**：user email/phone/ID/JWT/Authorization header/数据库连接信息脱敏。两个例外：schema 名称（fact_sales/dim_region）不脱敏（debugging 关键上下文）；SQL 不整体打码，做 **PII literal redaction**（如 `WHERE customer_phone = '<PHONE>'`），保住 SQL Debug 能力。

所有 timeout 必须产生对应 span（LLM/MCP/SQL/Agent failed），让 Langfuse 能回答「这次为什么慢」「失败发生在哪一层」。

Metrics 先记在 Langfuse + 本地：E2E latency P50/P95、LLM/MCP/SQL latency、Repair rate、Memory hit rate、E2E success rate。**不引入 Prometheus/Grafana/Alertmanager**。

### 十四、Evaluation（Golden Set 渐进 + 行为期望）

目录终态：`evaluation/{requirement, memory, retrieval, tool_selection, sql, repair, report, frontend, e2e}`。

- **Golden Set 从 Phase 0 开始**：`baseline_cases.json` 首版 **20~30 例**（P0 落 20~22 例），覆盖 11 类：普通对话 / 简单数据分析 / 多条件查询 / 多轮 / Context Reference / Schema Retrieval / SQL Failure / SQL Repair / MCP Failure / Report / Memory；随改造渐进扩到 30 → 50。**不要第一天建 500 题**。
- **必须有行为期望**，不只看 SQL 恰好对不对：

```json
{
  "query": "再按产品细分",
  "expected": {
    "memory": { "required": true, "types": ["conversation", "session"] },
    "retrieval": true,
    "clarification": false
  }
}
```

指标：Requirement Accuracy / Clarification Accuracy / Memory Recall Accuracy / Memory Pollution Rate / Recall@K / Tool Selection Accuracy / SQL Execution Success / Repair Success Rate / Report Factuality / Chart Correctness / E2E Success Rate。闭环：Trace → Evaluation → Failure Analysis → Prompt/Agent/Memory Optimization → Re-evaluation。

**评估维度（P14 展开）**：

- **Agent Evaluation**：Requirement Accuracy / Tool Selection Accuracy / MCP Success Rate / SQL Execution Success / SQL Repair Success / Report Correctness / Memory Behavior Accuracy。
- **Workflow Evaluation**：该澄清的时候是否澄清？该 Tool 的时候是否 Tool？不需要 Tool 是否避免调用？失败是否 Repair？无法 Repair 是否停止？
- **Memory Evaluation**：用户明确设置 preference → 是否保存？新 session → 是否读取？用户修改 preference → 是否覆盖？普通对话 → 是否污染？
- **Report Evaluation**：KPI correctness / Table correctness / Chart correctness / Insight grounding / No hallucinated numbers。
- **Regression Evaluation**：Prompt / Model / Tool / MCP / Agent 任一变更，都必须重跑 Golden Set。

**Baseline Metrics（P0 必记）**：Requirement Accuracy / Tool Selection Accuracy / MCP Success Rate / SQL Execution Success Rate / SQL Repair Success Rate / Report Generation Success Rate / Memory Hit Rate / End-to-End Success Rate / P50 Latency / P95 Latency。

### 十五、Testing（Playwright 两层 E2E）

四层：Unit(Vitest+pytest) → Component → API/Integration → Browser E2E。

- **Contract E2E**（deterministic/mock：前端+SSE+API contract+state+rendering）跑 PR；
- **Full System E2E**（real LLM+RAG+MCP+DB）跑 Nightly/release 手动门。不做每日唯一机制。

Playwright 场景（`frontend/e2e/specs/`）：happy-path、clarification、retry、empty-result、failed-result、report-version、background-execution、session-recovery、memory-multiturn。登录→新建分析→Requirement→Confirm→Report 主干必须覆盖。

### 十六、Frontend Contract（保留并强化）

一等公民不变：AnalysisPhase 状态机（idle → parsing → awaiting_missing → awaiting_confirm → generating → report_ready，异常 error、调整 adjusting）、`analysisReducer` 是 phase 唯一写入者、Backend State → Application Event → SSE → Frontend Reducer（不直通内部 state）。**Canonical Events 基线 = `docs/sse-v2.md` 现行 7 个**：`phase / requirement / trace / thinking / report / error / done`。**P11 在此基线上扩展 progress 事件（扩展，不替换）**：`agent.started / agent.thinking / agent.completed / agent.failed`、`tool.started / tool.completed`、`sql.generated / sql.executed`、`repair.started / repair.completed`、`report.generated / report.updated`——其中 `agent.thinking` ≈ 现 `thinking`，`tool.* / sql.* / repair.*` 由 `trace` 事件载荷细化；前端只消费公开事件契约，不猜 backend 内部 state。ReportVersion 是一级 Domain Object（GENERATING/DONE/ERROR 与 Query Result SUCCESS/EMPTY/FAILED 两组状态区分），ReportPaper 保持 EMPTY/FAILED 明确展示。

**ReportVersion V1 范围**：创建/查看/切换/继续调整/重新生成（已有能力，强化即可）。version diff/favorite/delete/compare 属 V2，非当前技术含金量核心。

### 十七、Legacy Policy（正式纳入 Phase 1）

- 建立顶层 `legacy/`（含 `agents/` / `tools/` / `adapters/` 子目录）归置旧链路（parent_graph legacy mode 等），标 `@deprecated`；不同时保留 `agents/legacy/`（见 §二·二）。
- 规则：**新代码禁止 import legacy 模块**（可用测试或 lint 断言钉住）；Phase 2~14 对 legacy 只允许 bug fix；Phase 15 删除。
- 杜绝「新链路 + 旧链路 + 新功能又偷偷接旧代码」的三岔路复发。

### 十八、Phase 排序与优先级（冻结）

```text
P0   Baseline + Golden Set          ← 最先做的是测量，不是改代码
P1   Architecture Freeze + Legacy Policy（产出五份设计文档 + legacy/ 归置 + 重写 CLAUDE.md「宪法版」）
P2   RAG / MCP Boundary             （MCP_ONLY flag → Phase 5 收口删 fallback）
P3   State & Context Runtime        （State 拆 Request/Requirement/Execution/Report/Runtime + 新建 context/{runtime,decision,policy,assembler}；Agent 不自拼 Context）
P4   Memory Lifecycle               （新建 memory/{conversation,semantic,query,policy,manager}：Recall Before Agent / Write After Reliable Event / 冲突更新）
P5   Tool / MCP Contract            （统一 Metadata + Protocol/Contract/Semantic/Failure/Integration 测试 + 停用本地 fallback）
P6   Unified LLM Migration          （正式迁模型：锁 llm-contract → Adapter → 配置收敛 → Golden Set Before/After）
P7   Prompt Refactor                （Requirement → Planner → SQL → Repair → Report）
P8   Execution Agent Loop           （Plan/Generate/Validate/Execute/Evaluate/Repair 闭环）
P9   Reliability Layer              （reliability/：Timeout/Retry/Error Classification/Recoverability/Background Task/Resume）
P10  Report Runtime                 （ReportSpec/ReportVersion/ReportBlock/ChartSpec/Insight + 三层 Fact Checker）
P11  Frontend / SSE Contract
P12  Playwright E2E
P13  Langfuse                       （LLM/Agent/Memory/MCP/SQL/Retry/Report spans + Redaction）
P14  Evaluation                     （各模块 Eval + 行为期望）
P15  Docs / README / Demo           （README / AGENTS.md / Architecture docs 刷新 + Demo；CLAUDE.md 仅做终稿打磨）
```

**Phase 门纪律（严格按序，不跳）**：每个 Phase 完成后必须走 `代码 → Unit Test → Integration Test → Golden Case → Git Commit → 更新 CLAUDE.md 对应章节`，再进入下一 Phase。不要出现「P3 还没稳定就开始改 Prompt，P6 模型又换了，P8 Agent Loop 又改 State」的交叉污染。

**逐 Phase 验收清单（吸收自 V2 完整版，Phase 门判定用）**：

- **P0**：[ ] Golden Set ≥ 20 [ ] 所有 case 可重复运行 [ ] 每例有 expected behavior [ ] baseline 已记录 [ ] baseline 可导出
- **P1**（实施 plan: p1-architecture-freeze，2026-08-26 落地）：[x] Agent 职责明确（agent-flow.md §五） [x] Workflow 职责明确（agent-flow.md §三） [x] State 字段明确（state-contract.md） [x] 无新代码 import legacy（test_legacy_import_freeze.py + legacyImportFreeze.test.ts 双侧钉死） [x] 架构图完成（五份文档内嵌 mermaid；P15 另出 7 张正式图） [x] CLAUDE.md 宪法版落地（88af63d）
- **P2**：[ ] ReportAgent 不直接 import RAG [ ] 所有 Schema Retrieval 走 MCP [ ] MCP input/output schema 固定 [ ] timeout 可控 [ ] failure 可识别 [ ] integration tests 完成 [ ] fallback 最终关闭
- **P3**：[ ] Agent 不自行拼完整 Context [ ] Context Runtime 唯一入口 [ ] Context 有优先级 [ ] context injection 可测试 [ ] 无 context duplication
- **P4**：[ ] Conversation/Session/Long-term Memory 各自正常 [ ] Long-term 可读可写 [ ] Explicit preference 可写入可更新 [ ] 冲突处理（supersede）[ ] 普通对话不污染长期记忆 [ ] Memory injection 有测试
- **P5**：[ ] 所有 Tool 有统一 description（含 use / don't use / examples）[ ] input/output schema 齐 [ ] Tool failure 标准化 [ ] ToolRegistry 唯一 [ ] Agent 不直接依赖工具实现
- **P6**：见 §九 6.12（11 项）
- **P7**：[ ] Prompt 分 Agent 管理 [ ] 无巨型 Prompt [ ] Tool Policy 明确 [ ] Negative Constraints 明确 [ ] Output Schema 明确 [ ] Prompt Version 可追踪 [ ] Golden Set 指标提升或至少无回归
- **P8**：[ ] 正常 SQL 成功 [ ] SQL failure 可识别 [ ] Repair 可触发且成功率可测 [ ] Retry 不超 budget [ ] 不可恢复错误不无限循环 [ ] Agent decision 可 trace
- **P9**：[ ] Timeout/Retry 可测试 [ ] 错误分类完成 [ ] 不无限 retry [ ] Agent 能区分 recoverable/non-recoverable [ ] 用户收到稳定错误信息
- **P10**：[ ] ReportSpec schema 固定并通过校验 [ ] Chart/Table 字段存在于 QueryResult [ ] KPI 来源可追溯 [ ] 不生成不存在的数据 [ ] ReportVersion 创建/查看/切换/重新生成正常
- **P11**：[ ] API/SSE event schema 固定 [ ] 前端可显示 Agent progress（Tool/SQL/Repair）[ ] Report 正确渲染 [ ] Error/Empty 状态正确 [ ] Session resume 正确
- **P12**：[ ] Playwright 配置完成 [ ] ≥ 10 核心场景（普通对话 / 简单报表 / 多轮澄清 / Context Reference / Schema Retrieval / SQL Success / SQL Repair / MCP Timeout / Report / Memory Preference / Version / Error Recovery）[ ] Contract E2E 稳定 [ ] Full E2E 可运行
- **P13**：[ ] 每次请求有 Trace [ ] Agent/LLM/MCP/SQL/Repair spans 齐 [ ] Prompt version 与 Model 可追踪 [ ] PII 脱敏 [ ] latency 可分析
- **P14**：[ ] Golden Set ≥ 20 [ ] 自动 Evaluation [ ] Agent/SQL/Report/Memory 指标齐 [ ] Regression detection [ ] baseline/optimized 对比
- **P15**：[ ] README 13 章齐 [ ] 架构图 7 张 [ ] ADR-001~007 [ ] CLAUDE.md 终稿（含 Forbidden Patterns）[ ] Demo

**必须完成线**：P0–P5、P8–P13 完成即达「优秀 Agent Engineer 面试项目」水平；P6/P7/P14 为强烈建议（可提前穿插但不阻塞架构收口）；P15 收尾包装。CI/CD、Prometheus、复杂 SLA 属可选增强，不为「工程化」把项目做成 DevOps 项目。

Phase 1 产出的五份锁定文档（后续 Claude Code 改造的「宪法」）：

```text
docs/architecture/agent-flow.md
docs/architecture/state-contract.md
docs/architecture/frontend-contract.md
docs/architecture/report-runtime.md
docs/architecture/memory-architecture.md
```

**CLAUDE.md 重写提前到 P1**（对齐总 Plan 结尾的执行序列 `Phase 0 → Phase 1 → 写新的 CLAUDE.md`；旧总 Plan 曾排在 P15，已被本基线取代）：五份文档锁定后立即按冻结基线重写 CLAUDE.md，只保留 Project Identity / Architecture Principles / Canonical Flow / Agent Responsibilities / Frontend Contract / Report Contract / Memory Architecture / Tool & MCP Contract / LLM Policy（只写「统一 reasoning model、provider 无关」，不出现具体 provider 名）/ State Contract / Timeout & Failure Policy / Observability / Change Discipline；历史实现、旧模型、历史 bug、迁移过程不再堆入。理由：P2~P14 施工期间每个 Claude Code 会话都以 CLAUDE.md 为第一上下文——若等到 P15 才改，施工全程读到的都是旧架构描述，必然产生指导漂移。P2~P14 各 Phase 落地时同步增量更新对应章节（如 P6 当天更新 Configuration 节），P15 只做终稿打磨与 README/AGENTS/Demo 收尾。

**P15 文档收口清单**：

- README 必备章节：Project Overview / Architecture / Agent Design / Memory / MCP / RAG Integration / LLM / SQL Repair / Report Generation / Frontend / Observability / Evaluation / Testing。
- 架构图 7 张：System Architecture / Agent Flow / Memory Flow / MCP Flow / Execution Loop / Frontend Event Flow / Observability。
- ADR 记录：ADR-001 Agent vs Workflow / ADR-002 RAG via MCP / ADR-003 Unified LLM / ADR-004 Memory Lifecycle / ADR-005 ReportSpec / ADR-006 SQL Repair / ADR-007 Langfuse。
- CLAUDE.md 终稿章节：Project Overview / Architecture / Directory Structure / Agent Responsibilities / State Contract / Context Runtime / Memory Rules / Tool Rules / MCP Rules / LLM Rules / Prompt Rules / Report Rules / Frontend Contract / Testing / Langfuse / Coding Standards / Forbidden Patterns / Legacy Policy。
- **Forbidden Patterns（写进 CLAUDE.md）**：不直接 import RAG；不让 Agent 自拼 Context；不让 Agent 直接访问 Memory DB；不让 Agent 直接调用 provider SDK；不让 Tool 没有 description；不让 Report Agent 编造数据；不无限 retry；不绕过 MCP；不新增 legacy import；不新建 generic 文件夹。

### 十九、验收标准

Architecture：单一 canonical flow ✅ Legacy 隔离 ✅ RAG 独立 ✅ MCP boundary 清晰 ✅ Agent 职责清晰 ✅ State 清晰。
Agent：Requirement 动态决策 ✅ Tool Selection 动态性 ✅ Execution feedback loop ✅ Repair 非 blind retry ✅ Report 结构化 ReportSpec ✅。
Memory：Session/Long-term 分离 ✅ Selective Recall ✅ Agent-specific Policy ✅ Write Policy 明确 ✅ Lifecycle ✅ Conflict Resolution ✅ Memory Evaluation ✅。
Frontend：Phase 状态机 ✅ SSE Contract ✅ Requirement→Confirm→Execute→Report ✅ ReportVersion ✅ SUCCESS/EMPTY/FAILED ✅ Playwright ✅。
MCP：Protocol/Contract/Semantic/Failure/Integration/Timeout 六类测试齐 ✅。
LLM（P6 验收全项见 §九）：旧配置移除 ✅ 统一 Adapter ✅ llm-contract.md 落档 ✅ 五类结构化输出稳定 ✅ Tool Calling/MCP 兼容实测 ✅ Migration Golden Set Before/After 通过 ✅。
Reliability：Timeout 分类 ✅ Retry limits ✅ Error envelope ✅ Recoverability ✅ Resume/Background ✅ Persistent failure state ✅。
Observability：PG Trace ✅ Langfuse ✅ 各类 spans ✅ PII redaction ✅。
Evaluation：Requirement/Memory/Retrieval/Tool/SQL/Repair/Report/Frontend/E2E ✅。

---

## Files to change

本 plan 本身**不改任何代码**——它是基线冻结。后续改动按 Phase 各自开 plan，模式预告（blast radius 由各 Phase plan 细化）：

| Phase | 主要触点 |
|---|---|
| P0 | `evaluation/baseline_cases.json`（新建，20~22 例）、baseline 测量脚本、本文档附 baseline 数字 |
| P1 | `docs/architecture/*.md` 五份（新建）、顶层 `legacy/` 归置、import 断言测试、**CLAUDE.md 宪法版重写** |
| P2 | `tools/data_tools.py` / `rag_schema.py` 收敛到 MCP、`PHASE2_MCP_ONLY` flag |
| P3 | `agent/*_graph.py` → `agents/{requirement,execution,report}/state.py` state TypedDict 拆分、新建 `context/{runtime,decision,policy,assembler}.py`、`models/contracts.py` |
| P4 | 新建 `memory/{conversation,semantic,query,policy,manager}.py`、semantic_entry 字段扩展 |
| P5 | `tools/registry.py` Tool Metadata 扩展、`tests/` 新增 MCP 五类测试、删本地 RAG fallback |
| P6 | `docs/architecture/llm-contract.md`（新建）、`app/llm/` Adapter 包（新建）、`strip_think` 迁入、`MINIMAX_*` 配置移除、Before/After 对比报告、CLAUDE.md Configuration 节当天更新 |
| P7 | Prompt 按 Agent 拆分（`agents/*/prompts.py`）、versioning 元数据、Negative Instructions / Tool Policy 落 prompt |
| P8 | `agent/sql_graph.py` Repair 上下文结构化（目标形态 `agents/execution/repair.py`） |
| P9 | `reliability/`（新建，顶层），收编 `llm_resilience.py` 语义 |
| P10 | 新建 `report/{spec,validator,versioning}.py`、`services/report_version_service.py` 扩展 |
| P11 | `analysisReducer.ts` / `api/*Stream.ts` 事件面整理（在 sse-v2 七事件基线上扩展） |
| P12 | `frontend/e2e/`（新建 Playwright 工程） |
| P13 | `observability/` Adapter + redaction 层、Langfuse SDK 接线 |
| P14 | `evaluation/` 各模块 harness |
| P15 | `README.md` / `AGENTS.md` 刷新 + Demo；CLAUDE.md 终稿打磨 |

## 复用现有工具

以下均已核实存在，各 Phase 设计时**先查这里再造新的**：

- `backend/app/context.py` — L1/L2 digest 覆盖重写 / L2.5 archive / L3 structured facts 四层，P4 直接作为 Conversation Memory 底座。
- `backend/app/infra/memory/` — `policy.py` / `user_memory.py` / `query_memory.py`（含 `record_failure()`）/ `memory_manager.py` / `mem0_extractor.py`；pgvector 打分 `0.6×sim + 0.2×importance + 0.1×LFU + 0.1×LRU` 与容量淘汰保留。
- `backend/app/llm_resilience.py` — `_TokenBucket` / `invoke_with_retry` / `_classify_retryable` / `LLMTimeoutError`；P9 收编为 `reliability/retry.py` 语义来源，不重写算法。
- `backend/app/services/report_version_service.py` — `persist_confirmed_run / persist_adjust_run / persist_empty_run / persist_error_run` append-only 落库，P10 直接扩展。
- `backend/app/infra/execution/registry.py` — ExecutionRegistry 后台任务 + 事件重放 + 轮询通知，P9 Background Task Timeout 挂在其上。
- `backend/app/tools/mcp_faq_client.py` + `faq_tools.py` — stdio MCP client 单例模式，P2/P5 的 MCP client 参考实现。
- `backend/app/utils/pii.py` `mask_pii` — P13 redaction 层复用其正则族。
- `backend/app/utils/text.py` `extract_sql`（首 SELECT 锚定 + 首 `;` 截断）/ `safe_json_parse` — P6 迁入 LLM Adapter。
- `backend/app/agent/security_guard.py` + `startup_guard.py` — Security 边界已有，不动。
- `frontend/src/stores/analysisReducer.ts` / `api/confirmStream.ts` — P11 在其上整理事件面，不推倒。
- 测试资产：`test_requirement_analysis_sqlgate.py`、`test_requirement_card_mirror.py`（前后端 parity）、`test_full_flow.py`（REPORTAGENT_E2E 门）。

## 验证

本 plan 的验证 = 「基线可测 + 每个 Phase 有门」：

1. **P0 交付物即第一道验证**：跑通并记录 `cd backend && pytest`、`cd frontend && npm run lint && npm run test:run`、`REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_full_flow.py -s` 的当前数字，连同 SQL success rate / Report success rate 写入本 plan 附录（或 P0 子 plan）；`baseline_cases.json` 20~22 例入库。
2. 每个 Phase 子 plan 自带 TDD 任务与验收命令（沿用现有 markers：smoke | contracts | graphs | persistence | api | e2e）。
3. 全程回归红线：任一 Phase 落地后全量 offline suite 必须保持绿；e2e 在 P12 前保持手动门。
4. 总验收见「十九、验收标准」清单；P15 时逐项打勾并写入新 CLAUDE.md。

## 明确不做

- ❌ **Prometheus / Grafana / Alertmanager / 复杂 SLA** —— Langfuse + 本地 metrics 足够；真部署再说。
- ❌ **完整 CI/CD 流水线** —— 本地命令矩阵 + Nightly Full E2E 手动门即可。
- ❌ **自动行为偏好学习（promotion pipeline）** —— V1 只有显式声明入 stable_preference。
- ❌ **LLM 自动拍 confidence** —— 规则固定：显式陈述/业务定义才入 active。
- ❌ **Report 数字的 HTML 正则审计** —— 只校验 `ReportSpec → QueryResult` 映射。
- ❌ **ReportVersion compare / delete / favorite / diff** —— V2 再议。
- ❌ **Session State 与 temporary_preference 物理分表** —— 逻辑分开、`agent.session` 字段区分，Evaluation 证明需要前不拆。
- ❌ **动态 / 按用户的 retry budget** —— 固定 SQL 2 / MCP 2 / LLM 2。
- ❌ **多模型路由** —— 单一 reasoning model，除非 Evaluation 证明有必要。
- ❌ **各 Agent 随手设置 generation 参数** —— 默认值集中 config；特殊覆盖须经测试证明并显式配置。
- ❌ **长期保留双 Retrieval 实现** —— local RAG fallback 到 Phase 5 必须移除。
- ❌ **第一天建大 Gold Dataset** —— 首版 20~22 例，渐进 30 → 50。
- ❌ **ReportAgent 内重新实现 embedding/chunking/vector search/rerank**。
- ❌ **跳过 Phase 顺序改代码** —— 先 P0 测量、P1 冻结文档，再动手。
- ❌ **把本伞形 plan 当实施任务单** —— 每 Phase 另开带 TDD 分解的实施 plan。

---

## 附录 A：冻结后的核心闭环图

```text
User → Frontend → Security → Context Runtime
  → Requirement Agent (Memory + MCP + Decision) → RequirementCard → Confirmation
  → Execution Agent (Memory + MCP + Planning;
      Plan → SQL → Validate → Execute → Evaluate ↘ Failure → Repair)
  → Report Agent (Query Result → ReportSpec) → ReportVersion → Frontend
  → Langfuse + Eval
```

从本 plan 登记起，开发问题从「还要不要加东西」切换为「这个改动属于哪个边界、解决哪个 failure mode、如何验证」。

## 附录 B：Definition of Done 总清单（最终验收，吸收自 V2）

每完成一个 Phase 按 §十八 的逐 Phase 清单判定；整个项目完成时按本清单终验，不凭感觉：

- **Architecture**：[ ] Agent/Workflow 边界清晰 [ ] State 边界清晰 [ ] Context Runtime 唯一入口 [ ] RAG 通过 MCP [ ] Legacy 不再被新代码依赖
- **Agent**：[ ] Requirement 能判断是否澄清 [ ] Execution 能自主选择 Tool、有 Planning [ ] 有 Repair Loop 且有 budget [ ] 能根据结果决定下一步
- **Memory**：[ ] Conversation/Session/Long-term [ ] Long-term 读/写 [ ] Preference 更新 [ ] 冲突消解 [ ] 污染防护
- **MCP**：[ ] contract [ ] Tool schema/description [ ] timeout [ ] retry [ ] invalid response [ ] empty result [ ] server unavailable
- **LLM**：[ ] 使用 ragent-py 同款模型（`Qwen/Qwen3-8B`@SiliconFlow，见附录 D）[ ] 旧模型配置删除 [ ] Unified Adapter [ ] Reasoning normalization [ ] Structured Output [ ] timeout/retry
- **Prompt**：[ ] Requirement/Execution/SQL/Repair/Report prompt [ ] Tool Policy [ ] Negative constraints [ ] Output schema [ ] Versioning
- **SQL**：[ ] schema retrieval [ ] generation [ ] validation [ ] execution [ ] error analysis [ ] repair [ ] max retry [ ] success/failure 分类
- **Report**：[ ] ReportSpec [ ] KPI [ ] Table [ ] Chart [ ] Insight [ ] Fact validation [ ] ReportVersion
- **Frontend**：[ ] API contract [ ] SSE contract [ ] Agent progress [ ] Tool/SQL/Repair 状态 [ ] Report 渲染 [ ] Error 状态 [ ] Version 状态
- **Testing**：[ ] Unit [ ] Integration [ ] MCP integration [ ] Contract E2E [ ] Full E2E [ ] Playwright [ ] Golden Set [ ] Regression evaluation
- **Observability**：[ ] Trace [ ] Agent Span [ ] LLM Generation [ ] Tool/MCP/SQL Span [ ] Error [ ] Latency [ ] Token [ ] Prompt version [ ] Model [ ] PII redaction

## 附录 C：12 个面试问题（项目含金量的判定标准）

以下问题都能从代码 / Trace / Evaluation / 架构图里拿出证据时，项目即达到完整的 Agent Engineering Project 水平：

1. 为什么 Requirement Agent 和 Execution Agent 要拆开？
2. 为什么 RAG 不直接 import，而通过 MCP？
3. Memory 什么情况下读取？什么情况下写入？为什么不会污染？
4. Agent 和 Workflow 的边界在哪里？
5. SQL 出错以后，Agent 怎么知道应该 Repair，而不是重新跑一遍？
6. Tool Description 为什么影响 Agent Tool Selection？
7. 换模型以后，怎么证明 Prompt 没有退化？
8. MCP timeout 怎么处理？
9. 一次请求失败以后，怎么从 Langfuse 找到是哪一层失败？
10. Report Agent 怎么防止编造数据？
11. 前端怎么知道 Agent 当前正在 Tool Calling、SQL Execution 还是 Repair？
12. 你怎么证明这次重构真的比原来的版本好？

## 附录 D：ragent-py 模型配置快照（2026-08-25 探查，P6 输入）

**目标模型（P6 迁移对象）**：

| 项 | 值 | 证据（`D:\PyProject\ragent-py`） |
|---|---|---|
| Model | `Qwen/Qwen3-8B`（免费、reasoning-capable） | `app/config.py:12` |
| Provider | SiliconFlow，OpenAI 兼容 | `app/config.py:6,10` |
| Base URL | `https://api.siliconflow.cn/v1` | `app/config.py:10` |
| Key env | `SILICONFLOW_API_KEY` —— **只进本地 `.env`，不得复制进 ReportAgent 仓库** | `.env:1` |
| Protocol | openai SDK（`AsyncOpenAI`）；ReportAgent 侧可走 `langchain_openai.ChatOpenAI`（与现 `llm.py` 同栈） | `app/llm/chat.py:11,71-75` |
| 默认参数 | temperature 0.7 / top_p 0.9 / max_tokens 4096 / timeout 90~120s / retry 2 次（共 3 次） | `app/llm/chat.py:106-118`；`app/llm/base.py:348-408` |

**对 P6 设计有直接影响的三个行为事实**：

1. **Reasoning 是 prompt 驱动的标签协议**，不是 provider 原生字段：ragent-py 用 system prompt 强制 `<think>...
