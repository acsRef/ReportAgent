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
| [2026-08-27-p3-context-runtime.md](2026-08-27-p3-context-runtime.md) | P3 实施：Context Runtime 骨架 + State 五块归位 + checkpoint 兼容（compatibility-first）——四件套 `runtime/decision/policy/assembler.py` + `app/context` 包化 facade（兼容路径不转发 runtime）+ State 5 块 TypedDict 视图 + checkpoint `(γ)` graph 入口节点单点 migrate（schema_version + MigrationError + deterministic 映射 + unmapped 保留）；P4 落地 Selective Recall 策略 + Memory structured records + _save_l3_facts 解耦 | 回链 [refactor-master-freeze](2026-08-25-refactor-master-freeze.md) §六 + [state-contract](../architecture/state-contract.md) §三 + [memory-architecture](../architecture/memory-architecture.md) §八/§九；**v1 review 已消化 P0×2 + P1×2 + 7 其他项；待实施** |
| [2026-08-26-p2-rag-mcp-boundary.md](2026-08-26-p2-rag-mcp-boundary.md) | P2 实施：RAG/MCP 边界——泛化 stdio MCP client（吸收 mcp_faq_client 模式）+ schema/字典/FAQ 三通道正路切 MCP、HTTP 直连降级 flag 管控 fallback + 五分类失败语义（禁静默空数组）+ 四类测试钉子（import/allowlist/schema/failure）+ 第六份架构文档 mcp-contract.md；PHASE2_MCP_ONLY flag，fallback 删除留 Phase 5 | 回链 [refactor-master-freeze](2026-08-25-refactor-master-freeze.md) §二/§八/§十八 P2；**Task 1+2+3 已落地 master（aa73a51 / bc5159c / 7baa3d0），4 轮 review 全部消化；Task 4/5 待开** |
| [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) | 重构冻结基线（伞形 plan，已合并 V2 完整版）：架构契约 / 目标目录冻结 / Memory & Context Runtime / Reliability / Report Runtime / MCP 边界 / Unified LLM Migration / Playwright / Langfuse / Evaluation，P0–P15 阶段门 + 逐 Phase 验收清单 + DoD + 12 面试问题 + ragent-py 模型快照 | 宪法级文档；各 Phase 启动时另开实施 plan 并回链本文件 |

## 已完成

| Plan | 主题 | 落地 |
|---|---|---|
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
