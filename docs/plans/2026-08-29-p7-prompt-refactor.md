# P7 实施：Prompt Refactor——按 Agent 拆分 + 六层结构 + Versioning + Golden 闭环

> 状态: 进行中
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §十 P7 完整版 + §十五 验收清单 + §十六 实施范围（P7 | Prompt 按 Agent 拆分 `agents/*/prompts.py`） + CLAUDE.md §14 Planning Discipline + [[memory:p6-review-landed]] §D2/D5 deferred 不阻塞 P7

## Context

### 伞形 plan §十 P7 完整版硬约束
- **分层（不要巨型 Prompt）**：每个 Agent 的 prompt 由六层组成——System Contract / Role / Task Contract / Tool Policy / Output Schema / Safety Policy；Dynamic Context 由 Context Runtime 统一注入，prompt 不自拼上下文。
- **分 Agent 职责与禁区**：
  - Requirement Prompt：理解意图、检测歧义、决定是否澄清、产出 RequirementCard。**禁止生成 SQL**。
  - Execution Prompt：Planning、Tool selection、SQL 生成、执行结果解读、Repair 决策。
  - Report Prompt：输入 RequirementCard + QueryResult + Report constraints + Memory preference，输出 ReportSpec。
- **Negative Instructions（每个 prompt 必备）**：Do NOT invent tables/columns；Do NOT fabricate query results；Do NOT assume unavailable schema；Do NOT call search_schema when schema is already known。
- **Tool Policy（显式写进 prompt）**：schema 信息不足 → 调 search_schema；schema 已在 context → 不重复调用；SQL 执行失败 → 先读 error 再决定 repair 策略。
- **Prompt Versioning**：每个 prompt 带 `name / version / purpose / input / output` 元数据，version 进 Langfuse（P13），改动可追踪。
- **Prompt Eval 闭环**：每次 prompt 变更必须走 `Golden Set baseline → 新 prompt → compare`；不接受「感觉这个 prompt 更好」。
- **新增 Prompt Rule 前先四问**：该由代码解决？Tool Contract 解决？State Contract 解决？Validator 解决？——都否才加 prompt rule。不无限堆 prompt。

### 现状盘点（2026-08-29，对照 commit `85245a2`）
| Prompt | 位置 | 形态 |
|---|---|---|
| 意图分类 | `backend/app/agent/intent.py:67` | 裸 f-string `prompt = f"""你是意图分类器..."""`，混合 dynamic context |
| 报告规划师 | `backend/app/agent/report_graph.py:61` | 裸 f-string，混合 dynamic context |
| 需求解析 | `backend/app/agent/requirement_parser.py:36` | `_PARSE_PROMPT` 模块化常量，但仍单段混合 |
| 意图分析 | `backend/app/agent/sql_graph.py:167` | 裸 f-string |
| SQL 规划 | `backend/app/agent/sql_graph.py:311` | 裸 f-string |
| SQL 生成 | `backend/app/agent/sql_graph.py:442` | 裸 f-string |
| 对话摘要融合 | `backend/app/memory/conversation.py:69` | 裸 f-string |

**7 处 prompt 全无六层结构、无 Versioning 元数据、无显式 Negative Instructions 段、无 Tool Policy 段。**

### 上游已完成（不可越界）
- P3/P4c Context Runtime 4 graph caller 已真接 `ContextRuntime.build()`，dynamic context 注入由 Context Runtime 统一负责——**P7 不重做**，只确保 prompt 模板占位符与 Context Runtime 输出对齐
- P5 Tool Contract 14 字段已统一，Tool Policy 段落可以**直接引用** `app/tools/registry.py: registry.all_tools()` 描述
- P6 LLM Adapter 已收敛（含 P6 review 异常语义修复），P7 不动 Adapter

## Design

### D1 Prompt 模块拆分：每个 Agent 旁一个 prompts 模块

```
backend/app/agent/prompts/
├── __init__.py                # 集中导出
├── intent_prompts.py          # 1 个: INTENT_CLASSIFY_V1
├── requirement_prompts.py     # 1 个: REQUIREMENT_PARSE_V1 (从 requirement_parser.py:36 迁移)
├── sql_prompts.py             # 3 个: SQL_INTENT_ANALYZE_V1 / SQL_PLAN_V1 / SQL_GENERATE_V1
└── report_prompts.py          # 1 个: REPORT_PLAN_V1

backend/app/memory/prompts/
├── __init__.py
└── conversation_prompts.py    # 1 个: CONVERSATION_SUMMARIZE_V1
```

**理由**：保持 "code goes to its narrowest existing boundary"（CLAUDE.md §二·Forbidden Patterns）；不新建 `utils2/` / `common2/` / `prompts_root/`。

### D2 6 层结构：每个 prompt 拆段 + 拼接函数

每 prompt 由 6 段组成 + 1 个 build 函数：

```python
# prompts/sql_prompts.py
SQL_GENERATE_V1 = {
    "system_contract": "...",     # Agent 边界（禁止生成 DROP 等）
    "role": "...",                # "你是 ReportAgent SQL 生成专家"
    "task_contract": "...",       # 具体任务描述
    "tool_policy": "...",         # schema 信息不足→调 search_schema 等
    "output_schema": "..."        # 明确 JSON 形状
    "safety_policy": "...",       # Do NOT invent tables/columns 等
}
SQL_GENERATE_META = {
    "name": "sql_generate",
    "version": 1,
    "purpose": "根据查询计划生成可执行 SQL",
    "input": ["plan", "schema_text", "dictionary_block"],
    "output": "SQL string"
}

def build_sql_generate_prompt(plan, schema_text, dictionary_block) -> str:
    return "\n\n".join([
        SQL_GENERATE_V1["system_contract"],
        SQL_GENERATE_V1["role"],
        SQL_GENERATE_V1["task_contract"],
        SQL_GENERATE_V1["tool_policy"],
        SQL_GENERATE_V1["output_schema"],
        SQL_GENERATE_V1["safety_policy"],
        f"# 输入\n计划：{plan}\n表结构：{schema_text}\n字典：{dictionary_block}",
    ])
```

**6 段集中常量**，build 函数负责拼接 + 注入 dynamic context 占位符。

### D3 Versioning 元数据：META dict + 进 trace sdk

每 prompt 配 `META = {name, version, purpose, input, output}`，5 字段。

`app/infra/trace/sdk.py` 增 `add_prompt_version(name, version)`（本 Phase 实现）。Langfuse 实际接入留 P13。

测试：`test_prompt_versioning.py` 验证 7 个 prompt 都含完整 5 字段。

### D4 Negative Instructions 必备段（每 prompt 含 4 条基线 + Agent 专属）

**基线 4 条**（来自伞形 plan §十）：
1. Do NOT invent tables/columns
2. Do NOT fabricate query results
3. Do NOT assume unavailable schema
4. Do NOT call search_schema when schema is already known

**Agent 专属**：
- Requirement：Do NOT generate SQL
- Execution/SQL：Do NOT output DELETE/UPDATE/DROP；Do NOT bypass safety_validator
- Report：Do NOT invent numbers; all numbers must come from QueryResult

测试：`test_prompt_negative_instructions.py` 用正则锚定（`Do NOT` / `严禁`）保证每条都进 prompt。

### D5 Tool Policy 显式段（不靠注释）

把现有"代码注释中"或"散落在 prompt 里的工具规则"集中到 `tool_policy` 段。

- Schema 已注入 context → 不重复调 search_schema
- Schema 不足 → 调 search_schema + get_schema
- SQL 执行失败 → 先读 error，再决定 repair（不是盲重试）

引用源：`app/tools/registry.py` 的 14 字段 description，P5 已 PASS。

### D6 Golden Set 闭环

复用 P0 资产 `evaluation/baseline_cases.json`（20 例含行为期望 + offline checker）。

**Before（重构前基线）**：用当前 7 处裸 prompt 跑 20 例，记：
- Requirement Accuracy
- Tool Selection Accuracy
- SQL Execution Success
- Repair Success
- Report Quality（人工抽检 3 例）
- Latency P50/P95

**After（重构后）**：用新 6 层 prompt 重跑同样 20 例，对比指标。

**判定**：P7 主要目的是**结构改造**，文案/温度参数**不动**（保持语义等价），因此 Before/After 指标应基本持平。任何显著退化（>5%）要回查 prompt 改写是否引入歧义。

新增 `docs/plans/p7-golden-before-after.md` 记录对比。

### D7 「新增 Prompt Rule 前先四问」

写进 plan §Explicitly NOT doing + 给后续 review 一个钉子：

- 该规则能用 Tool description 解决？→ 加 tool description
- 该规则能用 State contract 解决？→ 加 state 校验
- 该规则能用 Validator 解决？→ 加 query validator
- 该规则能用代码路径解决？→ 加 Python 代码
- 都否 → 才放进 prompt

## Files to change

| 路径 | 变更 |
|---|---|
| `backend/app/agent/prompts/__init__.py` | 新建：集中导出 7 个 prompt + 7 个 META + 7 个 build 函数 |
| `backend/app/agent/prompts/intent_prompts.py` | 新建：INTENT_CLASSIFY_V1 |
| `backend/app/agent/prompts/requirement_prompts.py` | 新建：REQUIREMENT_PARSE_V1（迁移自 requirement_parser.py:36） |
| `backend/app/agent/prompts/sql_prompts.py` | 新建：SQL_INTENT_ANALYZE_V1 / SQL_PLAN_V1 / SQL_GENERATE_V1 |
| `backend/app/agent/prompts/report_prompts.py` | 新建：REPORT_PLAN_V1 |
| `backend/app/memory/prompts/__init__.py` | 新建 |
| `backend/app/memory/prompts/conversation_prompts.py` | 新建：CONVERSATION_SUMMARIZE_V1 |
| `backend/app/agent/intent.py` | 调用 `build_intent_classify_prompt()` |
| `backend/app/agent/report_graph.py` | 调用 `build_report_plan_prompt()` |
| `backend/app/agent/requirement_parser.py` | 删 `_PARSE_PROMPT` 改用 build 函数 |
| `backend/app/agent/sql_graph.py` | 3 处替换为 build 函数 |
| `backend/app/memory/conversation.py` | 调用 `build_conversation_summarize_prompt()` |
| `backend/app/infra/trace/sdk.py` | 新增 `add_prompt_version(name, version)` 方法（本地记录） |
| `backend/tests/contracts/test_prompt_layering.py` | 新建：验证 6 段齐全 |
| `backend/tests/contracts/test_prompt_versioning.py` | 新建：验证 META 5 字段 |
| `backend/tests/contracts/test_prompt_negative_instructions.py` | 新建：验证基线 4 条 + Agent 专属 |
| `docs/plans/p7-golden-before-after.md` | 新建：Before/After 对比 |
| `docs/plans/README.md` | 登记本 plan 为「进行中」 |

## Reused

| 复用 | 路径 |
|---|---|
| LLM Adapter | `backend/app/llm/adapter.py:LLMAdapter.generate` —— P7 不动，P6 已收敛 |
| Context Runtime | `backend/app/context/runtime.py:ContextRuntime.build()` —— dynamic context 注入已统一 |
| Tool Registry | `backend/app/tools/registry.py:registry.all_tools()` —— Tool Policy 段引用 14 字段 description |
| Tools Prompt Format | `backend/app/llm/__init__.py:_format_tools_for_prompt()` —— Tool List 拼装已实现 |
| Safe JSON Parse | `backend/app/utils/text.py:safe_json_parse` —— JSON 容错 |
| Trace SDK | `backend/app/infra/trace/sdk.py:Tracer` —— 新增 `add_prompt_version` 方法 |
| Golden Set | `evaluation/baseline_cases.json` —— P0 资产，20 例含行为期望 |
| `_PARSE_PROMPT` 已模块化 | `backend/app/agent/requirement_parser.py:36` —— 迁移到 `requirement_prompts.py` |

## Verification

```bash
# 单测：6 层结构 + Versioning + Negative Instructions
pytest tests/contracts/test_prompt_layering.py -v
pytest tests/contracts/test_prompt_versioning.py -v
pytest tests/contracts/test_prompt_negative_instructions.py -v

# 5 处 caller 替换后 smoke 不回退
pytest tests/smoke/test_intent.py tests/smoke/test_report_graph.py \
        tests/smoke/test_requirement_parser.py tests/smoke/test_sql_graph.py \
        tests/smoke/test_conversation_memory.py -v

# 全量回归：contracts + smoke + graphs（基线 406+71+13）
pytest tests/contracts/ tests/smoke/ tests/graphs/ --tb=line -q

# Before/After 对比（手动门，金标准由 reviewer 拍板）
REPORTAGENT_E2E=1 python -m pytest backend/tests/e2e/test_full_flow.py -s
# 然后跑 evaluation/runner.py 对比 baseline_cases.json
```

**冒烟矩阵**：
1. 每个 prompt 6 段齐全（structural test）
2. 每个 prompt META 5 字段齐全
3. 基线 4 条 Negative Instructions 命中每 prompt
4. Agent 专属 Negative Instructions 命中对应 prompt
5. 5 处 caller 替换后 smoke 全绿
6. Before/After Golden Set 指标不显著退化（≤5%）

## Explicitly NOT doing

| 不做 | 理由 |
|---|---|
| 改 LLM Adapter（generate / generate_structured_safe） | P6 已 PASS，P7 不动 |
| 改 Context Runtime / assembler | P3/P4c 已 PASS |
| Langfuse 接入 | P13 范围，versioning 元数据本地存 trace sdk 即可 |
| 改 Execution Agent Loop 动态决策 | P8 范围 |
| 改 SQL Repair 的 prompt 决策逻辑 | P8 一起改；P7 只把现有 prompt 重构 |
| 删现有 prompt 文案 / 改温度参数 | P7 是结构改造，文案等价不动；调优留给 Golden Set 对比后的 P8/P14 |
| 引入 jinja2 / langchain PromptTemplate / 其他模板框架 | 零新依赖；f-string + 6 段 join 足够 |
| 新建 `prompts_root/` / `prompt_manager/` / `prompt_utils/` 通用文件夹 | Forbidden Pattern |
| 跨 Agent 复用 prompt 段到 base class | 段内容差异大；不强行 DRY，**显式 6 段**比抽象更重要 |

## TDD Tasks

### T1 Prompts 模块骨架
- [ ] Step1 新建 `app/agent/prompts/` + `app/memory/prompts/`（2 个 `__init__.py`）
- [ ] Step2 7 个 prompt 模块文件，每个含 6 段 dict + META + build 函数

### T2 6 层结构 + Versioning 测试
- [ ] Step1 `test_prompt_layering.py` 红 → 绿（验证每 prompt 6 段齐全）
- [ ] Step2 `test_prompt_versioning.py` 红 → 绿（验证 META 5 字段）
- [ ] Step3 trace sdk 新增 `add_prompt_version` 方法

### T3 Negative Instructions + Tool Policy 测试
- [ ] Step1 `test_prompt_negative_instructions.py` 红 → 绿（基线 4 条 + Agent 专属）
- [ ] Step2 Tool Policy 段引用 registry description

### T4 5 处 caller 切换到新 prompt build
- [ ] Step1 `intent.py` 替换
- [ ] Step2 `requirement_parser.py` 删 `_PARSE_PROMPT` 改 build
- [ ] Step3 `sql_graph.py` 3 处替换
- [ ] Step4 `report_graph.py` 替换
- [ ] Step5 `memory/conversation.py` 替换
- [ ] Step6 5 个 caller 的 smoke 全绿

### T5 Golden Set Before/After
- [ ] Step1 `docs/plans/p7-golden-before-after.md` 跑当前 prompt 基线（Before）
- [ ] Step2 跑新 prompt（After）
- [ ] Step3 写对比报告，指标不显著退化判定 PASS

## Why this design

P7 是「结构改造」不是「行为改造」：
- 文案等价不动 → 指标不退化是底线
- 6 层分段 + Versioning → 后续 P8 Agent Loop / P10 Report Runtime / P14 Evaluation 改造时改 prompt 段定位精确，不误伤其他段
- 「新增 Prompt Rule 前先四问」是防止 prompt 越堆越长的钉子
- 不引入新模板框架 → 零新依赖 + 不破坏 P6 收敛的 Adapter 调用形态

## Open questions

无（plan 写完后等用户审核）。