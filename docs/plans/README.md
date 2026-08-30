# docs/plans 永久索引（唯一入口）

> **本文件是 `docs/plans/` 的唯一永久入口。CLAUDE.md 永远指向这个固定路径——不要按日期找 plan。**
> 每日可再有 `YYYY-MM-DD-index.md` 做当天细分，但「该读哪份」以本文件为准。

## 怎么用（plan 驱动开发）

1. 任何非平凡改动动手前，**先读本文件**，在下表「进行中」区定位本次任务对应的 plan。
2. 找不到就 `grep -rl "^> 状态: 进行中" docs/plans/`（锚定行首的 `> 状态:` 标记行，避免命中本索引正文）。
3. 以该 plan 的「设计 / 复用工具 / 明确不做」为硬约束：复用优先、不越界、错误路径按 plan 枚举实现。
4. 开新 plan：命名 `docs/plans/YYYY-MM-DD-<slug>.md`，顶部写 `> 状态: 进行中`，并登记进本文件「进行中」表。
5. plan 落地后：状态改 `已完成`（带 commit），移入「已完成」表；被合并/取代的改 `已归档` 并注明并入哪份。

## 状态图例

| 状态 | 含义 |
|---|---|
| `进行中` | 正在实施，是当前开发依据 |
| `已完成` | 已落地并验证（带 commit / 完成报告） |
| `已归档` | 内容被合并或取代，保留作追溯，不再单独执行 |
| `暂缓` | 已批准但主动搁置（含残留项），重启时先读本文件与完成报告 |
| `只读评审` | review / grill 类，只审不改码 |

## 进行中

| Plan | 主题 | 备注 |
|---|---|---|
| [2026-08-30-p11-frontend-sse.md](2026-08-30-p11-frontend-sse.md) | P11 实施：Frontend / SSE Contract——registry live publish + trace progress（kind×status 细化，不新增事件类型）+ report 事件补 sse-v2 wire 形态 + 前端事件面统一（transport→schema→dispatch）+ ProgressCard 真信号 + session resume + P9-5 user_message 接线 + chitchat 终态修复 | 宪法 §9 / frontend-contract / 伞形 §P11 验收清单为硬约束；开工审计 8 findings 见 plan Context |
| [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) | 重构冻结基线（伞形 plan，已合并 V2 完整版）：架构契约 / 目标目录冻结 / Memory & Context Runtime / Reliability / Report Runtime / MCP 边界 / Unified LLM Migration / Playwright / Langfuse / Evaluation，P0–P15 阶段门 + 逐 Phase 验收清单 + DoD + 12 面试问题 + ragent-py 模型快照 | 宪法级文档；各 Phase 启动时另开实施 plan 并回链本文件 |

## 已完成

| Plan | 主题 | 落地 |
|---|---|---|
| [2026-08-30-p10-report-validator.md](2026-08-30-p10-report-validator.md) | P10 实施：Report Runtime——`app/report/` 域包三件套（spec/validator/versioning）+ ReportSpec v2（KpiSpec/TableSpec/DataBinding provenance，旧 payload 兼容 + contracts shim）+ 三层 Validator（structure 字段存在性 / numeric KPI 聚合重算 / fabrication 行存在性+膨胀检测；insight 文本不入正则审计）+ 父图接线 violations→FAILED + `ErrorCode.REPORT_VALIDATION_ERROR`（P9 码挂接生产者）+ decision trace；数据真实性从确定性工具的实现巧合变成被钉住的契约；前端 answer 契约零改动（vitest 259 持平） | p9-reliability 分支续作：`8249322`(plan) → `a3afb0e`(T1) → `6b9530a`(T2) → `e5839fb`(T3) → `163efc2`(T4/T5) → 收尾；Review-1：P10-1 行键 ⊆ fields + 拒空行（provenance 闭合）；全量 **920+ passed**；落地偏差 3 项 + Review-1 修 3 记 5 见 plan 落地记录 |

## 已完成

| Plan | 主题 | 落地 |
|---|---|---|
| [2026-08-30-p9-reliability-layer.md](2026-08-30-p9-reliability-layer.md) | P9 实施：Reliability Layer——`backend/app/reliability/` 顶层新包（errors/retry/backoff/timeout）+ ErrorEnvelope 10 码统一分类（AGENT/USER 两张 recoverable 表显式分离：DiagnosePolicy 与 SSE canRetry 各消费各表）+ llm_resilience 整体收编 retry.py（算法不重写，shim 保 legacy）+ LLM_MAX_RETRIES 默认收敛 2 + RETRY_BUDGETS 2/2/2 三处一致性钉 + MAX_TASK_DURATION 背景任务超时全链（persist_error_run(None) + TASK_TIMEOUT 事件）+ main.py 三个 SSE 出口收编（QUERY_* 用户码/文案换源、泛化出口 classify_exception、payload 契约不变）+ DiagnosePolicy 表驱动同源钉 | 分支 `p9-reliability`：`8e7f39a`(plan) → `f3c7a93`(T1) → `1f528ae`(T2) → `59a82ee`(T3) → `15d9150`(T4) → `77bdc37`(T5) → `c21a81d`(T6) → `feb46f6`(T7) → `692cd9f`(freeze 修复：errors.py 鸭子类型化守 P2 边界)；Review-1：P9-1 `_persist` session 终态参数化（error 不被覆盖回 report_ready `43a0516`）+ P9-2 提前退出统一 `tracer.end("FAILED")` `ea8b291`；P9-3/4/5 记录不修 |
| [2026-08-29-p8-execution-agent-loop.md](2026-08-29-p8-execution-agent-loop.md) | P8 实施：从硬编码 Retry 升级为 Agent 决策闭环——Evaluate→Diagnose→Route + DiagnosePolicy 纯确定性 + 7 要素 RepairContext + 预算 env-driven（MAX_SQL_REPAIR_RETRIES=2 / MAX_PLAN_RETRIES=1 显式命名）+ 失败分类细化（object/syntax/timeout/connection/permission/other）+ Tracer.add_decision；不改 6 段本体 + P8 不做 LLM Diagnose + SUCCESS pass-through 写 action="end" 与 "fail" 严格区分 + `_evaluate` 优先 validation（防 stale sql_result 跨 attempt 污染）+ `_generate_sql` 清 execution-derived state | `fix/p8-execution-agent-loop-review-fixes`：`9d5fd98` (Post-review Fix 14 findings + 2 反向钉) + `0a047c9` (Review-2 R1 stale-state + R2 dead-code + R3 state-hygiene + 3 R-test) + `43555cf` (Review-2 polish P2 graph test 名副实 + 1 polish 测试拆分净增) + Review-3 `cba81a4` (RV3-1 RepairContext error_kind 漂移修复 + 1 反向钉)；627 passed / 0 failed；详见 [p8-golden-before-after.md](p8-golden-before-after.md) |
| [2026-08-29-p7-prompt-refactor.md](2026-08-29-p7-prompt-refactor.md) | P7 实施：Prompt 按 Agent 拆分（6 层结构 + Versioning 元数据 + Negative Instructions + Tool Policy + Golden Set 闭环）——新建 `app/agent/prompts/` + `app/memory/prompts/` 7 个 prompt 模块（intent / requirement / sql×3 / report / conversation），5 处 caller 切换到 build 函数；trace sdk 新增 `add_prompt_version` 本地记录（Langfuse 接入留 P13） | 详见 [p7-golden-before-after.md](p7-golden-before-after.md)；5 commit（28b09e9 plan / 7cbe3e9 T1 / 141fae4 T2+T3 / 4400cc5 T4 / 44c9144 T5）+ a6e9246 late-import fix；586 passed / 0 failed 基线 |
| [2026-08-29-p6-llm-adapter.md](2026-08-29-p6-llm-adapter.md) | P6 实施：LLM Adapter 收敛 + remaining_token_budget 接通 + Golden 对比 | Adapter 骨架 + reasoning strip + LLM_* settings alias + 7处 Agent 迁移 + remaining 真传 + 285 contracts / 115 smoke 全绿 |
| [2026-08-27-p3-context-runtime.md](2026-08-27-p3-context-runtime.md) | P3 实施：Context Runtime 骨架 + State 五块归位 + checkpoint 兼容（compatibility-first）——四件套 `runtime/decision/policy/assembler.py` + `app/context` 包化 facade（兼容路径不转发 runtime）+ State 5 块 TypedDict 视图 + checkpoint `(γ)` graph 入口节点单点 migrate（schema_version + MigrationError + deterministic 映射 + unmapped 保留）；P4 落地 Selective Recall 策略 + Memory structured records + _save_l3_facts 解耦 | 8e146ed (plan) / d02da6b (T1 state 五块) / 62c4eba (T2 checkpoint adapter v1↔v2 + MigrationError) / 08471d5 (T3 graph `(γ)` migrate) / 2e37ef9 (T4+5 包重组 + ContextRuntime 四件套) / 3a539cc (e2e compat test)；p3 分支 614 passed / 0 failed / 1 warning（warning 为 facade 自身 DeprecationWarning，sql_graph 兼容路径 by design） |
| [2026-08-29-p4c-context-runtime-graph-integration.md](2026-08-29-p4c-context-runtime-graph-integration.md) | P4c 实施：ContextRuntime 真正接入主图 + assembler 真实装 + golden before/after——4 graph caller 翻转到 `ContextRuntime.build()`（requirement_analysis_graph._requirement_parse + confirmed_execution_graph._confirmed_sql_agent + requirement_parser.parse_requirement + sql_graph._plan/_generate_sql）；主链 3 smoke 钉（_requirement_parse / _confirmed_sql_agent / selective recall injection）；SelectiveRecallPolicy 24 钉（chitchat/empty + §三 Agent 表 + §二 semantic triggers + conversation triggers + vs LegacyFallbackPolicy diff + top_k budget）；assembler 真实 Filter（dedup by `(source, ref_id)` 保留 score 最高 + drop empty + §七 kind 排序 query>semantic>preference）+ Token Budget 截断（`P4C_ASSEMBLER_TOKEN_BUDGET` env，默认 4000 tokens ≈ 12000 chars）+ `remaining_token_budget` 入参（assembler `min(remaining, configured)` 公式）；golden before/after（离线 proxy 41 new 钉 + 84 contract + 40 baseline schema 全绿；真端到端 runner 留 P12 手动门）；诚实降级：graph caller 不传 fake remaining_token_budget（项目无 unified prompt budget 来源，等待 P5/P6 Unified LLM Migration 或后续 Context Budget 阶段）+ 防护钉 `test_graph_caller_does_not_invent_remaining_budget` | 9fefa44 (Task 1 graph caller 翻转 + spy mock 同步) / 8b16835 (Task 2 主链 3 smoke + ContextPolicyResolver strict prefix 修复) / 7d58eb0 (Task 3 selective 收益 24 钉——plan 原 8 用例与 selective 实际行为不一致 inline 重写为 property-based) / 23331aa (Task 4 assembler real + test_assemble_preserves_recall_items 改名 preserves_items_with_unique_keys 反映 dedup 契约 + caller agent string 修正) / 78bd6b7 (Task 5 docs(p4c-golden-before-after)) / **7675a51** (post-review fix #1: F1 默认 policy=SelectiveRecallPolicy + F2 remaining_token_budget 接口/算法 + F3 budget test 收紧 ≤300) / **beae759** (post-review fix #2: 主图 caller 真不传 fake remaining + 防护钉 + monkeypatch.delenv + plan §NOT doing 加诚实降级条目 + golden docs 加 deferred 段)；CLAUDE.md §15 红线全量离线 **681 passed / 1 skipped / 1 warning** (后 beae759 预计 683)；详情见 [docs/p4c-task2-main-chain-observation.md](../../p4c-task2-main-chain-observation.md) + [docs/p4c-golden-before-after.md](../../p4c-golden-before-after.md) |
| [2026-08-27-p4b-memory-lifecycle-selective-recall.md](2026-08-27-p4b-memory-lifecycle-selective-recall.md) | P4b 实施：Memory lifecycle + structured recall + SelectiveRecallPolicy——`semantic_entry` 加 scope/confidence/status/session_id/expires_at（migration 幂等+回填）；write pipeline 按 §五 固定规则（explicit→active / LLM-inferred→candidate 不召回，修 §五 违规）；`recall_structured()->list[RecallItem]` + source 枚举 + RecallItem 扩 kind/score/ref_id；ContextRuntime Step4 切换；SelectiveRecallPolicy（§二四触发 + §三 agent 表，contract 注入验证） | 7956a3e (plan) / e4fce7d (T1 枚举 + migration) / f5e3f02 (T2/T3 UserMemory lifecycle + active-only 召回) / 12fa9ad (T4 recall_structured + Step4 切换) / 64493bd (T5 write pipeline + SelectiveRecallPolicy + supersede + 跨测试污染修复)；p3 分支 614 passed / 0 failed；graph caller 翻转 + assembler Filter/Budget + golden 已落 P4c；cumulative review 待 P5/P6 前后启动 |
| [2026-08-27-p4a-conversation-memory-decouple.md](2026-08-27-p4a-conversation-memory-decouple.md) | P4a 实施：Conversation Memory 解耦 + L3 write seam——`app/memory/` 建为 domain/application 层（conversation.py + manager.py），`app/infra/memory/` 保持 persistence；context 包零 `infra.memory` 依赖（review #9 收口）；`recall` API 完全不变（`recall_structured` 明确 NOT doing，归 P4b） | efadf07 (plan) / 26a4d0e (解耦 + write seam)；grill 两边界已定（A 分层 + recall 不动）；597 passed / 0 failed |
| [2026-08-26-p1-architecture-freeze.md](2026-08-26-p1-architecture-freeze.md) | P1 实施：五份架构契约文档（docs/architecture/，双段结构防双向漂移）+ CLAUDE.md 宪法版重写（14 章 + Forbidden Patterns 十条）+ legacy 归置（后端 parent_graph/db→app/legacy/ + LEGACY BRIDGE BEGIN/END 锚点；前端 14 文件→src/legacy/）+ 双侧 import freeze 断言（red 验证通过）+ e2e 陈旧断言小修 | 26d900c / 8cd81e5 / fc04849 / 8c146e7 / 44fe071 / 88af63d；后端 384 passed / 前端 259 passed 基线不回退；执行偏差与挂起项见 plan 头部落地记录 |
| [2026-08-25-baseline-lock-golden-set.md](2026-08-25-baseline-lock-golden-set.md) | P0 实施：Baseline Lock + Golden Set（20 例含行为期望 + 离线可测 checker + 真实 API runner + 首份基线快照） | 16 pass / 0 fail / 4 skip 占位，sql_success_rate 1.0，P50 104s / P95 180s；附带发现 e2e 陈旧断言 + seed 仅 2024 数据 + COALESCE 假阳性风险（见 plan 附录） |
| [2026-08-12-draft-lock-release.md](2026-08-12-draft-lock-release.md) | confirm 成功后 draft 永久 locked 导致重新生成/adjust/PATCH 全被拒 | `_persist_report` 落库后 `release_lock`（locked→complete，幂等）；并发保护仍由 409 + lock 原语；真实链路：re-confirm v2 + adjust v3 均 SUCCESS、进行中仍 409；380 passed |
| [2026-08-12-execution-background-run.md](2026-08-12-execution-background-run.md) | 报告路径断连后台跑完：graph 从 SSE 解耦成独立后台任务 | 新 ExecutionRegistry（asyncio 独立任务 + 完成信号 + 事件重放）；三入口统一后台 runner，成功补 update_phase(report_ready)；409 SESSION_BUSY 拒绝重入；requirement-analysis 补 CancelledError；前端停止改「停止显示」+ 5s 轮询通知；真实浏览器验证：关浏览器→后台跑完→重开可见完整报告；376+256 passed；发现 adjust 遇 locked draft 现状问题（另开 plan） |
| [2026-08-10-ragent-token-cache.md](2026-08-10-ragent-token-cache.md) | ragent-py 登录 token 跨进程共享缓存（根治 429） | 三处同格式文件缓存（ragent mcp_server / interface_dict / mcp_schema_server）；401 自动失效重登；真实跨进程 E2E：进程 B 复用 A token 0 次新登录 |
| [2026-08-10-schema-from-rag.md](2026-08-10-schema-from-rag.md) | Schema 从 rag 来：删硬编码 _TABLES，三工具改查 ragent-py 字典 KB | rag_schema.py + data_tools 委托 + mcp registry 双份解析；过滤 dim_*/fact_*（系统表不污染）；词典 KB 已有 10 张表数据无需重灌；真实 E2E 全命中；351 passed |
| [2026-08-10-schema-faq-mcp-client.md](2026-08-10-schema-faq-mcp-client.md) | Schema FAQ RAG：ReportAgent stdio MCP client 连 ragent-py | 新 mcp_faq_client.py（持久后台循环 + stdio 会话，单例复用）；faq_tools.search_faq 优先 MCP、失败降级本地；_generate_sql 兼容 MCP chunk text 注入；真实 E2E：灌 20 FAQ + 5 查询全命中 |
| [2026-08-10-schema-faq-rag.md](2026-08-10-schema-faq-rag.md) | Schema RAG Phase 1：FAQ 知识库 + search_faq + SQL Agent 融合 | 知识库用 JSON 单一数据源（改 PG 表方案因 MCP/本地无 PG 依赖）；faq_tools.search_faq + `_generate_sql` prompt 注入（带「仅作参考」防御）；MCP 注册 search_faq 工具；20 条核实 SQL；9+317 tests 全绿 |
| [2026-08-10-llm-resilience.md](2026-08-10-llm-resilience.md) | LLM 韧性：令牌桶 10 req/s + 指数退避重试 + 90s 总超时 | 新建 llm_resilience.py（`_TokenBucket`/`_classify_retryable`/`invoke_with_retry`）；`call_llm` 走 `invoke_with_retry` + `max_retries=0` 关 langchain 内建重试；10+307 tests 全绿 |
| [2026-08-10-pg-pool-and-flush-timeout.md](2026-08-10-pg-pool-and-flush-timeout.md) | PG 连接池 10→20 + 60s 监控；trace flush 10s 总超时 | postgres.py `PG_POOL_MAX_SIZE=20` + `start/stop_pool_monitor`（`get_size/get_idle_size` 耗尽告警）；sdk.py `flush` 包 `wait_for` 10s 超时不重抛；main.py lifespan 接线；7+300 tests 全绿 |
| [2026-08-10-embedding-resilience.md](2026-08-10-embedding-resilience.md) | Embedding 韧性：超时 + 错误分类重试 + LRU 缓存 + trace span | service.py 单文件：`EMBEDDING_TIMEOUT/RETRIES/CACHE_SIZE` 可配；网络/限流/5xx 退避重试、认证/参数错误直接失败；LRU 只缓存成功；`current_tracer` span 埋点；12+300 tests 全绿 |
| [2026-08-05-e2e-regression-verification.md](2026-08-05-e2e-regression-verification.md) | e2e 回归验证：2026-08-04 / 2026-08-05 三份安全加固 plan | test_full_flow.py 1 passed in 62.12s（真实 LLM + PG，fact_sales.total_amount=3,502,666.04）；手工矩阵 5 项全过（chat 全角注入→SSE SECURITY_REJECTED；PATCH 卡字段→422；observability 隔离；非法 chosen_tool→静默置 None；ANALYSIS_DSN 真连 4/4 过） |
| [2026-08-29-p5-tool-mcp-contract.md](2026-08-29-p5-tool-mcp-contract.md) | P5 实施：Tool & MCP Contract 收敛（14 字段 + Registry validate + 四问 description + PHASE2_MCP_ONLY 默认 ON + mcp-contract.md 第六份架构文档；P2 残留 Task 4/5 收口） | 278 contracts + 115 smoke 全绿；686 passed / 3 failed（2 为 langgraph.checkpoint.postgres 缺失预存环境问题非回归 + 1 已修复 dictionary sqlgate）；mcp-contract.md 已冻结 |
| [2026-08-06-rag-dictionary-mcp-bridge.md](2026-08-06-rag-dictionary-mcp-bridge.md) | 数据字典 RAG 桥：ragent-py MCP + 字段语义澄清闭环 | A1-A8 ragent-py + B1-B6 ReportAgent + Phase C 跨进程冒烟已就绪；graphs 58 + contracts 14 + smoke 186 全绿，sqlgate 不回归；ragent-py 侧全量 195 passed；详见 plan 文档「最终落地」段 |
| [2026-08-05-security-guard-evasion-hardening.md](2026-08-05-security-guard-evasion-hardening.md) | SecurityGuard 注入变体加固：归一化前置 + 同义变形规则 | A-5 后半段：`_normalize`（NFKC + 剥零宽）+ 字符类 leet + 英文同义动词 + 中文绕过类；14 例绕过形态全拦、6 例新防误伤 + 既有面回归通过；全量 267 passed |
| [2026-08-05-pg-role-least-privilege.md](2026-08-05-pg-role-least-privilege.md) | PG 角色最小权限化：分析路径走独立非超级用户 | setup_app_role.sql + ANALYSIS_DSN + `_get_pg_conn` 切换；ragent_readonly 真连：pg_read_file/pg_authid/app schema 全部 PG 层挡；全量 274 passed |

## 已完成

| Plan | 主题 | 落地 |
|---|---|---|
| [2026-08-04-agent-security-hardening.md](2026-08-04-agent-security-hardening.md) | Agent 侧安全加固：SQL 危险函数/表白名单、/chat IDOR、trace 用户隔离、PII 补全 | A-1~A-6 全部落地；全量 242 passed（e2e skipped）；顺带修复 WITH…SELECT 被头闸误拦；dev 库 user_id 软迁移已执行 |
| [2026-08-03-security-injection-hardening.md](2026-08-03-security-injection-hardening.md) | 安全加固：注入规则修正 + confirmed 补闸 + PII 脱敏 | 28 安全测试全过；全量 186 passed；「以前的 prompt 都失效」可拦、正常查询无误伤 |
| [2026-08-03-sql-prompt-rules.md](2026-08-03-sql-prompt-rules.md) | SQL prompt 规则增强：JOIN 8 条 + 时间拆分 + 数组 `@>` + 空结果话术 | v2 修订：FK 进 generate_sql + 注入当前日期 + 删附注释规则；全量 158 passed |
| [2026-08-01-observability-ops.md](2026-08-01-observability-ops.md) | 可观测性运维闭环：指标 + trace 可视化 + agent 执行链路明细 | 后端 150 / 前端 245 passed；冒烟验证观测端点 |
| [2026-08-01-memory-mechanism.md](2026-08-01-memory-mechanism.md) | 记忆机制完善：分层上下文 + mem0 抽取 + LFU/LRU 容量淘汰 | 5 轮 23 测试；全套 146 passed；合并 conversation-context |
| [2026-08-01-postgres-checkpointer.md](2026-08-01-postgres-checkpointer.md) | PostgresSaver 替代 MemorySaver | 共享单例 + 三图接线 + lifespan；跨实例持久化测试通过 |
| [2026-08-01-draft-id-consistency.md](2026-08-01-draft-id-consistency.md) | P-4：confirmed-execution 锁定与执行的 draft 一致性 | load 定 draft_id 入 state，下游不重查 |
| [2026-08-01-extract-sql-multi-statement.md](2026-08-01-extract-sql-multi-statement.md) | P-8：extract_sql 多语句截断（安全加固） | 只取首条语句，截注入尾部 |
| [2026-07-30-bugfix-completion.md](2026-07-30-bugfix-completion.md) | 本轮 bug 修复完成报告（权威记录） | 后端 107 / 前端 242 / 启动冒烟 |
| [2026-07-30-auth-secret-hardening.md](2026-07-30-auth-secret-hardening.md) | B-1 auth 启动闸（fail-closed） | `startup_guard.py` + 10 测试 |
| [2026-07-30-query-execution-safety-and-reporting.md](2026-07-30-query-execution-safety-and-reporting.md) | SQL 执行安全 + 三态 + 报告正确性（主文档） | 层 2/6/7 全部落地 |
| [2026-07-30-cross-agent-state-fix.md](2026-07-30-cross-agent-state-fix.md) | 多 agent 状态隔离 + 上下文窗口保护 | C-1~C-4/C-6~C-9 落地；C-5 残留转 async |
| [2026-07-30-legacy-sql-bugs.md](2026-07-30-legacy-sql-bugs.md) | legacy SQL 子图三个 bug | Bug1/Bug2 落地；Bug3 随 async |
| [2026-07-30-tool-desc-error-examples.md](2026-07-30-tool-desc-error-examples.md) | 工具描述补错误返回样例 | `tools/__init__.py` |

## 暂缓（已批准但搁置，含残留项）

| Plan | 主题 | 搁置原因 |
|---|---|---|
| [2026-07-30-backend-async-refactor.md](2026-07-30-backend-async-refactor.md) | 后端全量 async 重构 | 经确认停在安全子集；两个真 P0 已用 `asyncio.to_thread` 单独修掉；全量改造需重写 5+ 测试、收益 P1 |
| [2026-08-29-p3-p4a-p4b-cumulative-review-and-fixes.md](2026-08-29-p3-p4a-p4b-cumulative-review-and-fixes.md) | P3+P4a+P4b cumulative review 修复清单（14 findings：5 必修 + 6 中修 + 3 可延后） | 项目已演进超出原 plan 范围（master 现含 P4c/P5/P6/P7 后共 586 passed 基线）；14 项 finding 中部分由后续 Phase 落实，部分仍 open。master HEAD `a6e9246` 后未回头修；暂缓窗口内仅作为「哪些 finding 已被新 Phase 替代」参考。重启时需逐项 re-check。 |

## 只读评审

| Plan | 主题 | 产出 |
|---|---|---|
| [2026-07-30-bug-review.md](2026-07-30-bug-review.md) | 核实各 plan bug + 补遗 | B-1~B-8 / P-1~P-8 |
| [2026-07-30-cross-agent-state-safety.md](2026-07-30-cross-agent-state-safety.md) | 多 agent 状态污染审查 | C-1~C-9 |
| [2026-07-30-design-principles-grill.md](2026-07-30-design-principles-grill.md) | plan 设计原则合规评审 | 逐份评分 + 整改 |

## 已归档（被合并/取代，保留追溯）

| Plan | 主题 | 并入 / commit |
|---|---|---|
| [2026-07-30-sql-row-cap-and-export.md](2026-07-30-sql-row-cap-and-export.md) | SQL 行数上限 + 超时 + Excel 导出 | 并入主文档；commit `e8e9b1e` |
| [2026-07-30-confirmed-exec-three-state.md](2026-07-30-confirmed-exec-three-state.md) | 三态分离 | 并入主文档；commit `56fb0fa` |

## 当日细分索引

- [2026-07-30-index.md](2026-07-30-index.md) — 2026-07-30 当天 plan 的关系网 / 优先级 / 冲突点细分

## 历史 plan（更早日期）

| Plan | 主题 |
|---|---|
| [2026-07-27-bugfix-atelier-migration.md](2026-07-27-bugfix-atelier-migration.md) | antd→atelier 迁移修复 |
| [2026-07-24-conversational-workbench.md](2026-07-24-conversational-workbench.md) | 对话工作台 |
| [2026-07-24-intelligent-analysis-workbench-design.md](2026-07-24-intelligent-analysis-workbench-design.md) | 智能分析工作台设计 |
| [2026-07-24-intelligent-analysis-workbench-html.md](2026-07-24-intelligent-analysis-workbench-html.md) | 工作台原型 HTML |
| [2026-07-22-frontend-ui-refactor.md](2026-07-22-frontend-ui-refactor.md) | 前端 UI 重构 |

## 约定回顾

命名、七章节结构、设计质量标尺、中文规范见 [CLAUDE.md](../../CLAUDE.md)「Planning Discipline」与 [AGENTS.md](../../AGENTS.md)。本文件只做索引与执行分流，不复述实现细节。
