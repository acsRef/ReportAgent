# P16 Memory Evaluation & Behavioral Tests

> 状态: 进行中
> 分支建议: `p16-memory-evaluation`　|　commit 信息统一带 `+ plan: p16-memory-evaluation`

## Context（为什么做）

用户投喂的分析（外部 review）判断：Memory 的**底层正确性测试已足够**（persistence 淘汰/promote/top_k=0/migration/vector recall/cross-user + policy 24 钉 + write pipeline contract），缺的是两层：

1. **Retrieval Quality**——"10 条 memory 问一个新问题，能否把正确那条召回来"的 Gold Set 指标评测（Recall@1/@3/MRR）。全仓确认：**零召回质量指标测试**，`evaluation/memory/` 与 `evaluation/retrieval/` 两个 harness 自 P14 起即 `deferred_keys` 占位。
2. **Agent Behavior**——"记忆被正确召回后确实改变 Agent 行为（SQL/Report），且不污染"。8 个行为测试中大部分不存在；先例仅有 decision 级 diff（`test_selective_recall_benefit.py`）与 prompt-capture 对照（`test_build_session_context.py`），**无任何"跑图断言 context 注入改变 SQL/Report"的先例**。

**调研中的三个实证修正**（决定本 plan 形态）：
- **report 链完全不吃 memory**：`report_graph.py` 不读 ContextRuntime，chart_config 由确定性 `chart_advisor` 产出（1 cat + 1 num、≤8 行 → pie / >8 行 → bar / 无 → table）。但契约 [memory-architecture.md](../architecture/memory-architecture.md) §二(2)「报告生成时召回图表偏好」+ §三 Report 行 = `Semantic ✅ Preference / Query ❌` 早已规定；`decision.py` REPORT 档位（`agent_policy==REPORT → pref_task=True`、`top_k_queries=0`）与 `policy.py` 前缀 `report_*→REPORT` 均已就位——**只差 graph caller 没接**（P4c 翻了 4 个 caller，report 是契约内遗漏）。→ 用户已拍板：补最小接线（按契约补全，非新增能力）。
- **EXECUTION 档可能把 preference 带进 SQL prompt**：`semantic.recall_structured` 召回 fact+preference 混合（按 `kind` 字段区分），契约 §三 Execution = `Semantic ✅ 业务事实`（不给 preference）。带「报告/图表」词的 query 在 EXECUTION 档会触发 semantic=True → 偏好文本进 SQL prompt → ② 测试会暴露真缺口 → 需一处小产品修。
- **fake embedding 下 Recall@k 无外部效度**：语义检索真依赖 SiliconFlow；离线只能钉「链路正确性」（同桶命中/负例排除/排序），可引用的数字需真 embedding env-gated 跑一轮。

**目标**：Memory 测试从"代码按设计运行"补到"召回正确 + 记忆让 Agent 变好且不污染"，Memory 封版；产出面试可讲的分层证据（Gold Set + 8 行为测试 + ON/OFF 对照）。

## Design

### W1 — Report 偏好接线（按契约补全）

**D1. ReportAgentState 扩展**（[report_graph.py](../../backend/app/agent/report_graph.py)）：加 `session_id: Optional[str]` / `user_id: Optional[Any]` / `report_preferences: list[str]`（默认 []）。父图 [confirmed_execution_graph.py](../../backend/app/agent/confirmed_execution_graph.py) report 段 ainvoke 入参加 `session_id=state["session_id"]`、`user_id=state.get("user_id")`（父 state 两者都在——`_confirmed_sql_agent` 同款取法）。

**D2. `_plan_analysis` 接入 ContextRuntime**（report_graph.py）：
- 节点改 `async def`（langgraph 支持 sync/async 混合；父图已 `await report_graph.ainvoke`）。照抄 `_confirmed_sql_agent`（confirmed_execution_graph.py:211-241）模式：局部 import `ContextRuntime`、user_id int 容错、`LLMConfig().context_window` 估 remaining budget、**try/except → warning → 空降级**。
- `agent="report_plan_analysis"`（resolver `report_*` → REPORT 档，decision 层已保证 semantic=True / query=False / top_k_preferences=3）。
- 从 `bundle["recall_items"]` 提取 preference 类条目（`source == "memory_preference" or kind == "preference"` 双保险）的 `raw_text` → `state["report_preferences"]`。
- **偏好不进 report_plan prompt**——chart 决策是确定性通道，prompt 零改动 → `REPORT_PLAN_META` version 不 bump、P12 mock keying 零风险、report_plan LLM 输出不可测面不扩大。conversation context（Report 少量档）本次不接（NOT doing）。

**D3. `apply_chart_preference` 纯函数**（新 [backend/app/report/preference.py](../../backend/app/report/preference.py)）：`(chart_config: dict, preferences: list[str]) -> dict`。
- 词表只认 `{柱状图/bar → bar, 饼图/pie → pie, 表格/table → table}`（chart_advisor 词表交集）；其余文本（"折线图/金额两位小数"）忽略。
- 语义（软约束、数据硬约束，对齐 §七 Conflict Priority）：原 type 为 `pie|bar` 且偏好 ∈ {pie,bar} → override type + **同步归一 dimensions 键**（pie 用 `category/value`、bar 用 `x/y`，互相转换）；原 type 为 `table`（数据不支持可视化）→ 偏好可视化类让位保持 table；偏好 table → 降级 `{"type": "table", "config": {"data": rows}}`。
- `_build_output` 末尾调用：`chart_config = apply_chart_preference(chart_config, state.get("report_preferences") or [])`（chart_advisor fallback 分支后、ComponentSpec 构造前）。
- chart type 词表不新增值 → 前端渲染零改动。

**D4. EXECUTION 档 drop preference**（[context/runtime.py](../../backend/app/context/runtime.py) Step 4 与 Step 5 之间内联，契约修正）：`agent_policy == EXECUTION` → 过滤 `kind == "preference"` 条目。REQUIREMENT（preference 影响需求理解）与 REPORT（preference 主消费方）不收窄。assembler 纯函数性零侵入。**注意既有测试适配**：P4c 主链 smoke / assembler contract 若注入 preference 类且断言 EXECUTION 档注入，需改用 semantic_fact 类或改断言（见 Files to change 受 adapter）。

### W2 — 行为测试集（真 PG + mock LLM + fake embedder，persistence marker）

安置 `backend/tests/persistence/`（无 DATABASE_URL 自动 skip，本地全量回归覆盖，符合项目惯例）。embedding 一律 patch 模块级全局 `app.infra.memory.user_memory.get_embedder` / `app.infra.memory.query_memory.get_embedder`（现有 `_FakeEmbedder` 模式，test_vector_recall_pg.py:28 同款）。mock LLM 用 P12 MockLLMAdapter（`LLM_PROVIDER=mock` + fixtures，report_plan kind 已覆盖）。写入用 `remember_explicit_preference`（manager.py:90，文本须命中 MemoryPolicy 正则，如"以后都用柱状图"）或 `UserMemory.save` 直插。

| # | 测试 | 断言核心 |
|---|---|---|
| ① | `test_report_chart_preference_behavior.py`：偏好「以后都用柱状图」落库 → 假 QueryResult（6 行区域数据）直跑 `report_graph.ainvoke`（mock LLM） | `chart_config["type"] == "bar"`（同数据无偏好对照组 → `"pie"`，**此即 ⑧ chart 侧 ON/OFF**） |
| ② | `test_memory_agent_layer_guardrails.py`：库里 stable_preference（柱状图）→ agent=EXECUTION、query 带「图表报告」词触发 pref_task | `ContextRuntime.build` recall_items 无 `kind=="preference"`（fact 可留）；SQL state 的 assembled_context 不含偏好文本 |
| ③ | `test_query_memory_sql_guidance.py`：`QueryMemory.save_query` 存历史成功 SQL（2024 区域 GROUP BY）→ agent=EXECUTION query「去年各区域销售」 | recall_items 含 source=memory_query；prompt-capture 断言参考 SQL 注入（test_build_session_context 模式）；对照组无 query memory → prompt 无参考块 |
| ④ | 同上文件：历史 COUNT(*)（订单量）query memory → query「统计销售额」 | 召回该参考（语义相近）但断言注入帧带「仅参考/Experience≠Truth」防御段（sql prompt 现役结构），SQL 本体不由 mock 定 |
| ⑤ | guardrails：直插 `status=candidate` 行 → build（requirement/report 档） | DB 行存在但 recall_items 无该行（active-only SQL），prompt 层同断言 |
| ⑥ | guardrails：直插 `expires_at < NOW()` 行 | 同上：DB 存在、召回与注入均无 |
| ⑦ | guardrails：A 的 stable_preference → B 同 query build | B recall_items 空（Agent 层 cross-user，repo 层已有 4 读路径钉） |
| ⑧ | 对照骨架并入 ① 对照组 + ③ 对照组 | 同 query 无/有记忆的 chart 与 prompt diff，各自钉死 |

### W3 — Gold Set 召回评测（evaluation/）

**G1. `evaluation/memory_gold_cases.json`**：~25 条记忆（A 用户：preference × 8 / semantic_fact × 8 / query memory × 6 / 干扰项 3）+ ~30 queries；每条 query：`agent`（requirement/execution/report 档）、`gold`（期望 top-k 命中条目的 `ref_key`）、`forbidden`（不得召回条目）。**gold 设计的硬约束**：fake embedder 同桶即中——dataset 的 query 措辞与其 gold 记忆共享确定性 token bucket，保证链路正确时必中、负例（如 Q3「GMV 口径」≠ 偏好类）不中。类型混淆例（Q「按区域销售」不召「按月」偏好、不召 COUNT(*) 订单量历史）显式设计。

**G2. 评测测试** `evaluation/tests/test_memory_gold_recall.py`（DATABASE_URL gate 同 persistence 惯例）：
- fixture 级一次直插记忆集（UserMemory.save / QueryMemory.save_query，semantic 行带 fake 向量）；
- **每个 case 前重置 access 副作用**（`UPDATE ... SET access_count=0, last_access_time=NULL`——混合分含 LRU 项，二次查询会变序）；
- 逐 case 断言：`ContextRuntime.build(agent=<case agent>)` 后 gold 的 raw_text ∈ 注入文本，forbidden ∉ recall_items（30 参数化）；
- 汇总打印 Recall@1 / Recall@3 / MRR / forbidden hit（fake 下仅链路正确性意义）。

**G3. 真 embedding runner** `evaluation/gold_memory_runner.py`：env-gated（DATABASE_URL + SILICONFLOW_API_KEY），同 dataset 真向量跑一遍输出 Recall@1/@3/MRR 写快照——**面试可引用的数字**（用户文档预期 ~Recall@1 80%+ / @3 95%+ 量级，不预设阈值，结果如实记录）。

**G4. 真 LLM SQL 行为手动门**（复用 REPORTAGENT_E2E=1 门与真 PG+MCP+LLM 环境）：3 case——② 的"偏好不偏 SQL"（`GROUP BY region` 保持）、③ 的历史参考续 SQL（结构同、时间条件新）、④ 的 COUNT(*)→SUM 不被污染。断言真 SQL 结构（非文本相等），模式同 P15 real e2e。

## Files to change

- `backend/app/context/runtime.py` — D4 Step 4.5 EXECUTION drop preference
- `backend/app/agent/report_graph.py` — D1/D2/D3 接线（State 字段 + async `_plan_analysis` + `_build_output` apply）
- `backend/app/agent/confirmed_execution_graph.py` — report 段 ainvoke 补 session_id/user_id
- `backend/app/report/preference.py` — 新，纯函数
- `backend/tests/contracts/test_chart_preference.py` — 新，R1 纯函数矩阵（pie→bar / 无偏好恒等 / 非类型文本忽略 / 偏好 table / 数据不支持时让位 / dimensions 键归一）
- `backend/tests/persistence/test_report_chart_preference_behavior.py`、`test_memory_agent_layer_guardrails.py`、`test_query_memory_sql_guidance.py` — 新（W2 ①~⑧）
- `evaluation/memory_gold_cases.json`、`evaluation/tests/test_memory_gold_recall.py`、`evaluation/gold_memory_runner.py` — 新（W3）
- `docs/plans/README.md`（登记）+ `CLAUDE.md` §6 现状行 + plan 落地记录
- **受 adapter**：P4c 主链 smoke / assembler contract 中若注入 preference 类断言 EXECUTION 档（D4 行为变更）——先跑回归定位再改断言（改用 semantic_fact 类条目或改断言为 fact 注入）

## Reused existing utilities

- `ContextRuntime().build`（context/runtime.py:44）+ `SelectiveRecallPolicy` REPORT 档位（decision.py:100/108-110）——接线只差 caller
- `_confirmed_sql_agent` 接入模板（confirmed_execution_graph.py:211-241）：局部 import / user_id 容错 / LLMConfig 预算估算 / try-except 空降级——**逐字照抄**
- `chart_advisor`（sql_tools.py:394）——**不改签名**，其输出作 `apply_chart_preference` 输入
- `remember_explicit_preference`（memory/manager.py:90）造 active 偏好；`UserMemory.save`（infra/memory/user_memory.py:47）直插任意 status/expires_at/source
- `_FakeEmbedder` 模式（tests/persistence/test_vector_recall_pg.py:28 等 5 处）+ 模块级 patch 两处 `get_embedder`
- MockLLMAdapter kind+seq keying（app/llm/mock.py）+ `tests/fixtures/llm_responses/*.json`（report_plan 已覆盖）
- prompt-capture 对照模式（tests/test_build_session_context.py:103-131）
- 真链路 env-gated 先例（evaluation/tests/test_real_rag_mcp_e2e.py，REPORTAGENT_E2E 门）

## Verification

```bash
# 离线全量（本地有 PG）：contracts + smoke + graphs + persistence 零回归
cd backend && pytest            # 期望 ~1120+ passed（1116 baseline + P16 增量）

# evaluation（gold set 30 参数化全绿）
cd evaluation && pytest tests/test_memory_gold_recall.py -v   # 或仓库级 pytest evaluation/

# 真 embedding 指标（手动门，出可引用数字）：
#   env: DATABASE_URL + SILICONFLOW_API_KEY
python evaluation/gold_memory_runner.py          # 输出 Recall@1/@3/MRR 写快照

# 真 LLM SQL 行为（手动门，后端+MCP+PG 全启动 + REPORTAGENT_E2E=1 + 真 LLM key）：
#   ② GROUP BY region 不被偏好偏 / ③ 参考续 SQL 结构 / ④ 不产 COUNT(*)
```

## Explicitly NOT doing

- **不实装 P14b memory/retrieval harness**（Langfuse trace observation 级，留 P14b）
- **不做 conversation memory 质量评测**（digest 摘要质量 LLM 主观）与 **report 链 conversation 注入**（契约 Report Conversation 少量，本次只做 Preference 主项，CLAUDE.md §6 现状注记）
- **偏好不进 report_plan prompt**、不改 `chart_advisor` 签名 / registry / 前端（chart type 词表零新增）
- 不做 insight/文本风格偏好；折线图等词表外类型偏好忽略（产品诚实）
- 不动 query_template 表结构与 lifecycle（无 status 列是既有设计，⑤⑥ 不适用于 query memory）
- gold set 不设绝对阈值断言（fake embedding 指标无外部效度；逐 case 硬断言 gold∈/forbidden∉；数值由真 embedding runner 产出如实记录）
- **不做简历数据盘点与浏览器截图**（用户排最后；seed 真实化已合 master `ab545b7`，P16 落地后另议）

## Task 分解（commit 序）

- T0 plan 落库 `docs/plans/2026-09-04-p16-memory-evaluation.md` + README 登记 + 开分支
- T1 D4 runtime EXECUTION drop preference（+ 回归定位适配受 adapter 测试）
- T2 D3 `app/report/preference.py` 纯函数 + contracts 单测矩阵（离线）
- T3 D1/D2 report 接线 + `test_report_chart_preference_behavior.py`（①+⑧ chart 对照，真 PG）
- T4 W2 其余行为测试（②③④⑤⑥⑦，真 PG）
- T5 W3 gold set：dataset + 评测测试 + 真 embedding runner
- T6 全量回归（backend + evaluation）→ 真 LLM/真 embedding 手动门（用户在场跑，结果如实记录）→ CLAUDE.md §6 + README 收尾
