# P15 e2e bug 修复 + 正式用例固化

> 状态: 进行中

## Context（背景）

P15 prelude（已合 master `d83e9d9` + 5 follow-up fix commit）用**真 RAG MCP + PG** 真跑 5 类 case 后暴露 4 个问题（详见 `docs/plans/2026-09-02-fix-sql-classification-and-e2e.md` + memory `p15-e2e-rag-handoff`）：

| # | 现象 | 根因（已定位） |
|---|---|---|
| ① | case 3 fault 不触发——「查询 unicorn_data」被 Requirement Agent 静默软化或销售额 | 无确定性丢噪声指令，是 schema 白名单（`rag_schema.py:210`）+ parse prompt「Do NOT invent」(`requirement_prompts.py:65`) + `RequirementCard` **无「点名对象缺失」通道**（`models/requirement.py:48`，5-key whitelist `requirement_parser.py:131` 丢弃其余）三者叠加，逼 LLM 只能对齐已知指标 |
| ② | case 5 supplement 第 2 轮不继承 time_range | `parse_requirement` 的 `prior_card` 只做 version+1（`requirement_parser.py:198`），**字段合并从未实现**；全库调用点都不传 prior_card；prompt 无「已确认约束承上轮」槽（`requirement_prompts.py:82`） |
| ③ | 每次 `ContextRuntime.build failed` 脏日志 | asyncpg 无 pgvector codec，`user_memory.py:163` / `query_memory.py:70` 把 float list 绑 `$1::vector` → 每次 `DataError (expected str, got list)` → assembled_context 恒空（也连累 ②） |
| ④ | probe case 价值低、无断言 | 被删骨架 `evaluation/tests/test_real_rag_mcp_e2e.py` case 3/4/5 无断言 teeth，case 2 无法证明 repair 真触发 |

用户拍板（2026-09-02 对话）：
- ① = **产品修复 + env-gated fault seam** 双管齐下（产品层补「点名对象 ∉ schema → 澄清不静默替换」；测试层 fault seam 供正式 fail/repair 用例确定性触发 execution 层真 fault）。
- ② = **全部已确认约束继承**（time_range/scope/metric/granularity/comparison 中第 1 轮已确认字段带为默认，仅本轮显式改写才覆盖）。
- ③ = **init_pool 统一注册 vector codec**（已 live 验证）。
- ④ = 5 case 固化为正式测试（`evaluation/tests/` + `REPORTAGENT_E2E=1` gate，带真断言），替换「几百个旧单测」的验证地位。

## Design（设计）

### T1 ③ vector codec 注册（先做，其余 T 的基础——不修它 ② 的 assembled_context 与 memory recall 全是空的）

`backend/app/infra/db/postgres.py` `init_pool`：给 `asyncpg.create_pool(..., init=_init_vector_codec)`。`_init_vector_codec(conn)` 用 `await conn.set_type_codec("vector", encoder=…, decoder=…, format="text")`——encoder 把 `list[float]` → `"[x,y,…]"` 字面量，decoder 解析 vector 文本回 list。覆盖全部 4 个向量绑定点（2 检索 + 2 INSERT），**零新依赖**（不装 pgvector python 包）。

已 live 验证（scratch pool + `<=>` 检索 + INSERT 均通过，无 DataError）。

**维度单一来源（P0-1 修正）**：当前运行时是 `EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B` 但 **`EMBEDDING_DIM=1536`**（`.env`；`init_pg.sql` 两处 `intent_embedding VECTOR(1536)`；`main.py:72` `VECTOR_DIM=int(os.getenv("EMBEDDING_DIM", "1536"))` 启动校验 1536==1536；live embed 实测返回 1536）。任何测试不得硬编码 1536 或 1024——fake embedding 长度一律从**同一个源** `os.getenv("EMBEDDING_DIM")`（同 main 启动校验与 init_pg 列）读取，`[0.01] * dim` 生成。这样钉住的是「config → embedding service → PG vector 列 → `<=>`」整条 contract，将来模型/维度迁移测试自动跟随，不产生 mismatch。

### T2 ② supplement 全部已确认约束继承

语义：第 1 轮确认卡（draft status=complete）的全部字段作为第 2 轮默认值；第 2 轮 LLM 只判「本轮新提出/改写的字段」，已确认字段不得再报 missing。

**State Contract（P0-2 修正）**：不引入临时 underscore 字段。`RequirementAnalysisState`（`requirement_analysis_graph.py:47`，`TypedDict(total=False)`）**声明式加正式 channel** `mode: Optional[str]`；`main.py` `_chat_requirement_analysis` initial state（:613-626）加 `"mode": request.mode`。requirement 图由 main 直接 `graph.ainvoke(initial)`（无父图嵌套），声明过的 channel 必然到达 `_requirement_parse`。state-contract.md §三 已把 RequirementState 子集映射到本文件（不逐字段枚举），无需改架构文档。`fault_override`（T4）在 ConfirmedExecutionState 同样声明式加入，不用下划线。

1. **调用点补 prior_card**：`requirement_analysis_graph._requirement_parse` 读 `state.get("mode") == "supplement"`：为 supplement 时经 `requirement_service.get_latest_card(session_id, user_id)`（现成）取上一轮卡（gate `status=="complete"`），把卡本身 + 序列化 prior 块传给 `parse_requirement`。
2. **merge 契约（区分「未提及」与「显式改写」）**：
   - **信号约定**：presence = 本轮明确改写，absence = 未提及。prompt 明确要求——上轮已确认字段若本轮**未提及/未改写**，LLM 输出该字段**留空**（禁止臆造、禁止照抄上轮值）；只有本轮真正改写的字段才输出非空值。
   - **合并算法**（`requirement_parser.py`，新卡构造后）：`target_metrics/time_range/scope/analysis_methods` 新值**非空 → 覆盖 prior**；**空 → 继承 prior**。dimensions 是粒度/对比等 hint 的载体，按 kind 标签并集去重、本轮同 kind 覆盖旧值。之后重算 `missing_fields`（被 prior 继承满足的 key 从 missing 移除）+ 重算 `status`（无 missing 且 assumption 全表态 → complete）。version 沿用 `prior.version+1`。
   - **merge 矩阵（offline parser contract，mock LLM 固定输出）**：

     | 第 2 轮 query | LLM 输出（presence） | 期望结果 |
     |---|---|---|
     | 再看月度趋势 | time/scope/metric 留空，granularity=月 | 继承 time=2024/scope=华东/metric=销售额，granularity=月 |
     | 改成 2025 年的月度趋势 | time_range=2025年 | time 覆盖=2025，scope/metric 继承 |
     | 改看华南 | scope=华南 | scope 覆盖，time/metric 继承 |
     | 再对比去年同比 | comparison 提出（非 missing） | comparison 入卡，其余继承 |
     | （本轮什么都没提的极端） | 全字段留空 | 完整继承上一轮，无 missing |

3. **prompt 加槽**（`requirement_prompts.py` `build_requirement_parse_prompt`）：第 4 个输入「已确认需求（承上轮；未在本轮改写的字段输出留空，由系统继承）」+ 缺失判定只针对本轮新表达的字段；经 `_call_llm_for_parse` 传入。

### T3 ① 产品修复：点名对象 ∉ schema → 澄清，不静默替换

`requirement_prompts.py` parse prompt 加规则：**若用户明确点名要查的表/对象不在可用表结构或字典里，禁止静默丢弃或替换成别的指标；必须产出 assumption `key` 以 `requested_object` 开头**（text 说明哪个对象缺失、可用的是哪些）。复用现 assumption 机制——`accepted=None` → status=missing → 前端 awaited 确认，天然把「静默替换」变成「显式澄清」。parser/模型零改动（assumption 已透传）。

确定性局限如实标注：依赖 LLM 遵守新指令，正式 e2e case 用行为不变量断言（见 T5），若回归软化该 case 即红。

### T4 ① 测试 seam：env-gated 请求级 fault injection

目标：正式 fail/repair 用例在同一 backend 上**逐 case** 确定性触发 execution 层真 fault（修复循环真恢复 / 永久 fault 真不伪造成功），不依赖 LLM 首猜拼错。

- **新模块** `backend/app/reliability/fault_inject.py`：`FaultSpec` 解析 + `kind_override(state) -> str|None`。契约：
  - 激活条件**双 gate fail-closed**：backend env `REPORTAGENT_E2E=1` **且** 请求带 `X-E2E-Fault` header `"kind=<k>;mode=<once|persistent>"`；否则恒 None（生产零行为变化）。
  - `kind` 白名单：`object_not_found`（repair 语义，按 validation-failed 注入）+ `permission`（fail-fast 语义，按 execution-failed 注入，DiagnosePolicy `not agent_recoverable → fail`）。
  - `once` = 仅第 1 次 attempt 注入（`retry_counters.sql_generation == 1` 判定）→ 修后第 2 次走真路径；`persistent` = 每次都注入 → 预算耗尽/永久失败。
- **接线**：`ConfirmedExecutionState` 声明式加正式 channel `fault_override`（同 P0-2 原则）；`main.py` chat/confirm 入口读 header（gate 后）→ 放进 confirmed execution initial state；`confirmed_execution_graph._confirmed_sql_agent`（:239 显式输入 dict）加 `"fault_override": state.get("fault_override")`；`sql_graph._evaluate` 顶部在真实判定前 consult `fault_inject.kind_override(state)`：
  - `object_not_found` → 返回 `VALIDATION_FAILED(kind=object_not_found)` → DiagnosePolicy → `retry_mcp_schema_retrieval`（**真** get_table_ddl + 替换 schema）→ re-generate。
  - `permission` → 返回 `EvaluateResult FAILED(kind=permission)` → DiagnosePolicy → `action=fail` → 图 FAILED。
- **once 判定与 counter 时机（防 off-by-one）**：increment 点在 `sql_graph.py:590`（`_generate_sql` 内 `retry["sql_generation"] = … + 1`，generate 之后、evaluate 之前）→ **第一次 evaluate 时 counter==1**。故 `once` = `sql_generation == 1`（首 attempt 注入，修后第 2 次 counter==2 走真路径）；`persistent` = 恒注入。每次 confirm 图以 `{plan:0, sql_generation:0}` 起（:239），once 语义按单次 confirm 归零。
- 单元可测：`_evaluate` 本身纯函数（只读 state），fault_inject.kind_override 纯函数 → offline contract 钉死判定逻辑；live e2e 钉真链路。

### T5 ④ 正式 e2e 用例文件

重建 `evaluation/tests/test_real_rag_mcp_e2e.py`（自 gate：`pytest.mark.skipif(not os.getenv("REPORTAGENT_E2E"), …)`，`REPORTAGENT_E2E_BASE_URL` 可配），复用 `evaluation/runner.py` 现成 driver（httpx + login + SSE `_stream_sse`/`_data_of` + fill-all PATCH + confirm + max-version report fetch）。用例与断言（全部零售订单 schema，查询锚定 2024）：

| case | 驱动 | 断言 |
|---|---|---|
| happy explicit | "2024年各区域销售额排名" → fill-all → confirm | card complete + time_range 2024年 + report `execution_status=SUCCESS` + `query_snapshot.sql` 含 fact_orders/order_amount + `answer.table.rows ≥ 1` |
| repair 确定性 | "2024年华东销售额" → confirm 带 `X-E2E-Fault: kind=object_not_found;mode=once` | 终态 SUCCESS + table 非空（证明 retry_mcp_schema_retrieval 真触发真恢复）；可选软断言 trace 出现修复步骤 |
| fail 永久 fault | "2024年华东销售额" → confirm 带 `X-E2E-Fault: kind=permission;mode=persistent` | 硬条件：**无 SUCCESS report version**（`NOT(execution_status==SUCCESS)`）∧ SSE 以 error/终止结束（QUERY_* 或 ReportVersion FAILED 落库）；不伪造成功 |
| 点名对象澄清（原 case3 软化） | "查询 unicorn_data 表的所有数据" | 硬条件（防最危险回归「软化→complete→SUCCESS」）：`NOT(requirement complete)` ∧ **无 SUCCESS report** ∧（卡含 `requested_object` assumption 或回到 missing/awaiting 待确认） |
| schema_retrieval | "订单相关的数据都在哪些表里？" | 结构化断言优先：正常终态 + 无 error + 卡/answer 中**至少出现 fact_orders 与 fact_payments 两个 token**（不依赖完整 wording，弱文案依赖） |
| multi_turn supplement | 轮1 "2024年华东销售额"(new→confirm) + 轮2 "再看月度趋势"(supplement) | 轮2 card 继承 time_range=2024年（scope/metric 也应在），fill 后 confirm 出 v2 SUCCESS |

修复/命中证明的硬契约仍在 offline（`test_diagnose_policy_object_path.py`）；本文件是**真链路行为不变量**（env-gated，manual/nightly）。

## Files to change（文件改动）

| 文件 | 变更 | 任务 |
|---|---|---|
| `backend/app/infra/db/postgres.py` | `_init_vector_codec` + `create_pool(init=…)` | T1 |
| `backend/app/main.py` | ① supplement 分支 requirement initial 加 `mode: request.mode` ② chat/confirm 读 `X-E2E-Fault` header（双 gate）→ confirmed initial state `fault_override` | T2/T4 |
| `backend/app/agent/requirement_analysis_graph.py` | `RequirementAnalysisState` 声明式加 `mode` channel；`_requirement_parse`：`mode==supplement` 时读 prior confirmed card 传给 parse + prior 块 | T2 |
| `backend/app/agent/requirement_parser.py` | 实现 prior_card 字段合并（presence 覆盖 / absence 继承）+ missing 重算 + 传 prior 块进 prompt | T2 |
| `backend/app/agent/prompts/requirement_prompts.py` | ①「已确认需求承上轮」槽 + 「未改写字段输出留空」约定 ② requested_object 不静默替换规则 | T2/T3 |
| `backend/app/reliability/fault_inject.py` | 新：FaultSpec + `kind_override`（双 gate + once/persistent + kind 白名单 + counter 时机判定） | T4 |
| `backend/app/agent/sql_graph.py` | `_evaluate` 顶部 consult fault override（object_not_found→VALIDATION_FAILED / permission→FAILED；once=`sql_generation==1`，:590 increment 已证） | T4 |
| `backend/app/agent/confirmed_execution_graph.py` | `ConfirmedExecutionState` 加 `fault_override` channel；`_confirmed_sql_agent` sql 子图输入 dict 透传 | T4 |
| `evaluation/tests/test_real_rag_mcp_e2e.py` | 重建正式用例（自 gate + 6 场景真断言） | T5 |
| 测试新增 | `backend/tests/contracts/test_vector_codec.py`、`backend/tests/contracts/test_fault_inject.py`（gate/白名单/**counter=0/1/2 矩阵**/两种注入形态）、`backend/tests/contracts/test_requirement_merge_supplement.py`（mock LLM 钉合并矩阵纯逻辑）、`backend/tests/graphs/test_supplement_prior_card_smoke.py`（`mode=supplement` 真到达 `_requirement_parse` 并转发 prior_card，spy parse_requirement）、`backend/tests/persistence/test_vector_search_pg.py`（真 PG + monkeypatch embedder，维度从 `EMBEDDING_DIM` 读取） | 各 T |

## Reused existing utilities（复用工具）

- `requirement_service.get_latest_card`（现成，读上一轮 confirmed card）
- assumption 机制 / `RequirementMissingField` 受控选项 / phase 计算（main.py:661-666）——T3 不新开字段
- `evaluation/runner.py` driver（`_stream_sse`/`_data_of`/login/fill/confirm）——T5 复用，不重写
- `reliability/errors.py` `SQL_ERROR_KINDS` / `AGENT_RECOVERABLE_KINDS`（单一来源）——fault kind 白名单以其为据
- asyncpg `set_type_codec`（标准库级，无新依赖）
- DiagnosePolicy / retry_mcp_schema_retrieval caller / `_evaluate`（T4 只在其上做 override 注入，不重写）

## Verification（验证）

- **T1**：`test_vector_codec`（encoder/decoder 纯）+ `test_vector_search_pg`（真 PG insert+`<=>` 检索无 DataError；monkeypatch embedder 返回 `[0.01]*dim`，`dim = int(os.getenv("EMBEDDING_DIM"))`，**不硬编码**）+ repro 脚本复跑确认无「ContextRuntime.build failed」。
- **T2**：`test_requirement_merge_supplement` offline 钉**合并矩阵**（presence 覆盖 / absence 继承 / 改写覆盖 / missing 重算，对照 plan 矩阵表）；`test_supplement_prior_card_smoke` 钉 `mode=supplement` 在 main→requirement 图→`_requirement_parse` 全链路可见并转发 prior_card（P0-2）。
- **T3**：正式 e2e 点名对象 case（LLM 依赖，行为不变量断言：`NOT complete ∧ 无 SUCCESS report ∧ 澄清 surface`）。
- **T4**：`test_fault_inject` offline（gate/白名单/**counter=0/1/2 矩阵**——counter==1 才 once 注入、>=2 不注入、persistent 恒注入/两种注入形态）；`_evaluate` 单测（fault_override 在 state → 返回注入 kind）。
- **增量回归**（按 [[test-strategy-skip-full-regression]]）：`backend/tests/contracts + smoke + graphs + persistence` + `evaluation/tests`。
- **全量回归**（Phase 收尾前最后一次）：backend 全量 + evaluation；master 不回退（基线 1008 PASS / 1 SKIPPED）。
- **Live e2e 手动门**：PG + ragent-py MCP + backend（`REPORTAGENT_E2E=1` 起）→ `REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_real_rag_mcp_e2e.py -v`，6 case 全绿；观察确认 permission-fail 终态真实形态后收紧断言。

## Explicitly NOT doing（不做事项）

- **不**实现 P14 mock 单测回填（已删，用户拍板不算数）。
- **不**修 DiagnosePolicy 的 validation_failed 分支对 permission/timeout 盲 retry_sql 的既有语义（pre-existing quirk，非本批目标；T4 seam 的 permission 走 execution-fail 路径绕开它）。
- **不**给 Requirement 加启发式中文词元↔schema 比对（不可靠）；requested_object 靠 prompt + 行为不变量断言兜底。
- **不**做 fault seam 的后端 env 之外的更宽激活（仅 `REPORTAGENT_E2E=1` + header 双 gate，fail-closed）。
- **不**动 ragent-py（独立仓库；`render.py` 改版已在 KB 数据里生效，其 repo commit 另排）。
- **不**做 memory 0 行数据回填（codec 修好后由正常链路自然累积）。
- **不**改 ② 为把 supplement 统一进 adjust 机制（两套语义收齐是大重构，留给 P15 主 plan）。
