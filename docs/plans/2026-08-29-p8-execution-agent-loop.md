# P8 实施：Execution Agent Loop——从硬编码 Retry 升级为 Agent 决策闭环

> 状态: 已完成（含 Post-review Fix：1 真 bug + 5 spec-deviation/correctness + 3 dead-code/重复 + 4 文档对齐；**Review-2 增：1 stale-state correctness + 2 测试强度**）
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §三 Canonical Flow + §四 Agent Responsibilities (Execution Agent: Plan/Generate/Validate/Execute/Evaluate + Diagnose/Repair, 预算 MAX_SQL_REPAIR_RETRIES) + §十一 Timeout & Failure Policy + CLAUDE.md §14 Planning Discipline + [[memory:p4c-landed]] / [[memory:p6-review-landed]]
> 协作: B 顺序第三 plan；P5/P6/P7 已落地后开 P8，本 plan 仅 P8

## Context

### 伞形 plan §三 Canonical Flow 要求
```text
Execution Agent (Plan → Generate → Validate → Execute → Evaluate ↘ Failure → Repair)
```

### 伞形 plan §四 Execution Agent 职责
> 主循环 Plan→Generate→Validate→Execute→Evaluate→Diagnose→Repair（七要素上下文回灌），预算 `MAX_SQL_REPAIR_RETRIES`
> 禁止：blind retry、无限循环

### 伞形 plan §十一 Retry 预算
- SQL repair 2 / MCP 2 / LLM transient 2
- Permanent 不 retry
- Agent-recoverable 走 repair
- DB Timeout ≠ SQL 错——区分 Query Timeout / Connection Failure / Permission / Object Not Found / Syntax

### 现状盘点（对照 commit `a6e9246` master HEAD）
**已有线性骨架**（[sql_graph.py:570-589](backend/app/agent/sql_graph.py#L570-L589)）：
```text
plan → generate_sql → validate
                  ↘ execute → evaluate → build_output → END
                       ↘ (validate fail) ─→ evaluate ─┐
                            (evaluate 决策：plan / generate_sql / END)
```

**`_evaluate` 节点**（[sql_graph.py:457-502](backend/app/agent/sql_graph.py#L457-L502)）：
- raw 空 + sql_retries < 3 → SQL_SYNTAX_ERROR（router 走 generate_sql 重试）
- error.kind ∈ timeout/connection/permission → FAILED（router 走 END，**不重试**）
- error 存在 + sql_retries < 3 → SQL_SYNTAX_ERROR
- error 存在 + plan_retries < 1 → SCHEMA_ERROR（plan 重来）
- 其他 → NEED_CLARIFICATION（END）

**`_route_after_evaluate`**（[sql_graph.py:559-567](backend/app/agent/sql_graph.py#L559-L567)）：4 分支硬编码路由

### 现状问题（按伞形 plan §四 / §八 / §十一）
1. **硬编码 if-else 决策，不是 Agent 决策**——evaluate 既做结果判定又做路由决策，未拆分为 Evaluate→Diagnose→Route（伞形 plan §四 Evaluate→Diagnose→Repair）
2. **预算硬编码 `3` 和 `1`**（不是 `MAX_SQL_REPAIR_RETRIES` 配置项）——伞形 plan §十一 "Retry 固定预算：SQL repair 2"，现 `sql_retries < 3` 意味着最多 3 次 repair，与契约冲突
3. **没有 `Diagnose` 节点**——evaluate 直接给 verdict，缺少「错误分类 + 决策依据」结构化输出
4. **7 要素上下文回灌不全**——generate_sql 重试只拼 prev_sql + error，缺 original_requirement / Current Schema / validation_result / retry_count / hint / fewshot（伞形 §四 7 维度）
5. **失败分类粒度粗**——只分 timeout/connection/permission 不重试，object（字段不存在）/ syntax（语法）/ other（其他）都用同条 SQL_SYNTAX_ERROR 路径
6. **决策可观察但不可解释**——@traced_node 已有，但 evaluate 的决策依据（为什么 route 到 plan 而不是 generate_sql？）没有结构化记录

## Design

### D1 引入 Diagnose 节点（Agent 决策点）——Evaluate→Diagnose→Route

职责拆分（拍板：Evaluate=发生了什么 / Diagnose=为什么+怎么办 / Route=下一步走哪）：

- `_evaluate` **保留为确定性判定节点**：消费 `sql_result` / `validation_result`，产出标准化 `EvaluateResult{status, kind, error, validation_result}`（判定 vs 路由拆分的边界：`_evaluate` 写 `execution_status` 字符串作为给父图的契约字段，但路由键由 `_route_after_diagnose` 按 `DiagnoseDecision.action` 读——F15 拍板）
- `_diagnose` 消费 `EvaluateResult` + `retry_counters` + `error_history`，调用 `DiagnosePolicy.decide()` 产出结构化 `DiagnoseDecision`：
  ```python
  class DiagnoseDecision(BaseModel):
      action: Literal["retry_sql", "replan", "clarify", "fail", "end"]  # F3: "end" 用于 SUCCESS pass-through，与 "fail" 严格区分
      reason: str               # 决策依据（"object_not_found: column X not in fact_sales"）
      error_kind: str           # 错误分类
      recoverable: bool         # 是否可恢复
      retry_target: Literal["generate_sql", "plan", "end"] = "end"  # 路由冗余字段，仅供 trace 用，路由由 action 直接驱动（F2 拍板）
      hint: Optional[str]       # 给下轮的修复提示
      confidence: float         # 决策置信度 0-1
  ```
- `_route_after_diagnose` 根据 `DiagnoseDecision.action` 路由：`retry_sql→generate_sql` / `replan→plan` / `end→build_output` / `clarify|fail→END`；execution_status 缺失时退化读 execution_status（P7 兼容）
- `DiagnosePolicy` 为**纯确定性规则策略**（基于 error_kind + retry_counters + validation_result），P8 不做 LLM 策略；符合 `Agentic where uncertainty exists / Deterministic where correctness matters`，LLM-assisted diagnosis 留待 Evaluation 证明不足后再议

### D2 失败分类粒度细化（伞形 plan §十一）

| kind | recoverable | 默认动作 | 说明 |
|---|---|---|---|
| `syntax` | ✓ | retry_sql | SQL 语法错，可修 |
| `object` | ✓ | retry_sql → replan | 表/字段不存在，需重读 schema |
| `timeout` | ✗ | fail | 超时无意义重试（按 §十一） |
| `connection` | ✗ | fail | 连接失败无意义重试 |
| `permission` | ✗ | fail | 权限不足无意义重试 |
| `other` | ✓ | retry_sql | 其他可重试 |

注：当前 `_evaluate` 把 timeout/connection/permission 标"不重试"——保留；object 在 kind 列表但走 SQL_SYNTAX_ERROR 路径——D2 显式分出。

### D3 预算 env-driven（伞形 plan §十一）

替换硬编码 3/1：
```python
MAX_SQL_REPAIR_RETRIES = int(os.getenv("MAX_SQL_REPAIR_RETRIES", "2"))
MAX_PLAN_RETRIES = int(os.getenv("MAX_PLAN_RETRIES", "1"))
```
- `MAX_SQL_REPAIR_RETRIES=2` 对齐伞形 §十一 "SQL repair 2"（现 `sql_retries < 3` 为 3 次 repair，与契约冲突，P8 收敛为 2）
- `MAX_PLAN_RETRIES=1` 为现有 `plan_retries < 1` 的显式命名化，非 P8 新增预算；文档中明确标注避免“为何 SQL 2、Plan 1”误解
- 执行序：`grep -rn retry_counters` 找现有测试依赖 3 的用例 → 修 expectation 3→2 → 再 env 单测 `monkeypatch.delenv/setenv` 钉 env 真生效 → 实现 → 全量回归

### D4 7 要素上下文回灌结构化（伞形 plan §四）

`generate_sql` / `plan` 重试时构造结构化 `RepairContext`，注入 prompt（7 维度对齐伞形 §四：Original Requirement / Current Schema / Previous SQL / Failure Category / Error Message / Validation Result / Retry Count）：

```python
@dataclass
class RepairContext:
    original_requirement: str       # 原始需求（user_query）
    plan: QueryPlan                 # 当前查询计划（保留对象，prompt 不渲染 repr——F4 拍板，避免 Pydantic __str__ 污染）
    target_metric: str              # 业务目标
    prev_sql: str                   # 上一次 SQL
    error: str                      # 错误信息
    error_kind: str                 # Failure Category
    validation_result: dict | None  # Validation Result
    retry_count: dict               # Retry Count（sql_generation / plan）
    hint: Optional[str]             # diagnose 给出的修复提示
    # schema 仅引用：caller 不再传 schema_context_ref，schema 由 6 段 schema_text 注入；F7 拍板
    # fewshot 由 6 段 task_contract 的 faq_block 注入，repair 段不再重复截断版本；F8 拍板
```

`build_sql_generate_prompt` 增 `repair_ctx: Optional[RepairContext]` 可选参数，重试时结构化拼接到 6 段 prompt 末尾。
P7 已留 hook（prompt 末尾拼接 retry feedback）——P8 把硬编码拼接改成 RepairContext 结构化；删去 P7 残留在 `_generate_sql` 末尾的 compat path 硬编码兜底（与上面 if 块条件完全相同，F5 拍板）。
**F1 关键**：`_plan` 入口必须保留 `_diagnose` 写入的 `retry_counters`，**不要**无条件 reset；否则单次分析最坏 4 次 SQL retry，`MAX_SQL_REPAIR_RETRIES=2` 契约失效。`_plan` 用 `counters = dict(state.get("retry_counters") or {}); counters.setdefault("plan", 0); counters.setdefault("sql_generation", 0)` 模式。

### D5 Decision Trace（伞形 plan §四 "Agent decision 可 trace"）

`_diagnose` 决策进 trace sdk：

```python
tracer.add_decision(
    name="sql_diagnose",   # F14 拍板：关键字统一为 name=（与代码 Tracer.add_decision(self, name, **fields) 一致），plan 早期伪代码写 node= 是 typo
    action=decision.action,
    reason=decision.reason,
    error_kind=decision.error_kind,
    retry_counters=retry_counters,
    execution_status=execution_status,
)
```

`Tracer.add_decision(name, **fields)` 新方法（计划性 stub，P13 Langfuse 接入时落库）。
SUCCESS pass-through 路径同样写 decision（action="end"，F3 拍板），保留决策可观察性，但与 "fail" 严格区分。

### D6 GoalTest：执行闭环可测

`tests/graphs/test_execution_agent_loop.py` 新建：
- 模拟 SQL syntax error → repair → 重试成功（断言：retry_counters.sql_generation += 1，最终 SUCCESS）
- 模拟 object not found → diagnose 走 replan（断言：retry_counters.plan += 1）
- 模拟 timeout → diagnose 走 fail（断言：不进入 retry，execution_status=FAILED）
- 模拟 budget 耗尽 → diagnose 走 clarify（断言：execution_status=NEED_CLARIFICATION）
- 模拟 SUCCESS → action="end"（F3 拍板，与 "fail" 区分）
- 模拟 replan 入口 → `_plan` 不重置 sql_generation 计数（F1 钉，避免 retry 翻倍）

## Files to change

| 路径 | 变更 |
|---|---|
| `backend/app/agent/sql_graph.py` | 引入 `EvaluateResult` / `DiagnoseDecision` / `RepairContext` / `DiagnosePolicy`；保留 `_evaluate` 为判定节点，新增 `_diagnose` 决策节点与 `_route_after_diagnose`；env-driven 预算 `MAX_SQL_REPAIR_RETRIES=2` / `MAX_PLAN_RETRIES=1` |
| `backend/app/agent/prompts/sql_prompts.py` | `build_sql_generate_prompt` / `build_sql_plan_prompt` 增 `repair_ctx: Optional[RepairContext]` 可选参数；6 段 prompt 末尾拼接结构化 repair feedback（schema 仅引用） |
| `backend/app/infra/trace/sdk.py` | `Tracer.add_decision(name, **fields)` 新方法（本地记录；P13 Langfuse 落库） |
| `backend/tests/contracts/test_diagnose_policy.py` | 新建：DiagnoseDecision 6 种 kind 决策路径全覆盖（纯确定性） |
| `backend/tests/contracts/test_max_sql_repair_retries_env.py` | 新建：env 真生效 + 默认值 2/1 + monkeypatch 钉；含 grep 现有用例 3→2 迁移 |
| `backend/tests/contracts/test_tracer_decision.py` | 新建：add_decision 记录 + span 关联 |
| `backend/tests/graphs/test_execution_agent_loop.py` | 新建：4 个 goal test（repair / replan / fail / clarify）走 Evaluate→Diagnose→Route |
| `backend/tests/contracts/test_prompt_repair_context.py` | 新建：build_sql_generate_prompt 接受 repair_ctx 后 prompt 含结构化 7 要素 |
| `docs/plans/2026-08-29-p8-execution-agent-loop.md` | 本 plan（拍板修订版） |
| `docs/plans/p8-golden-before-after.md` | Before/After 对比（决策可观察性 / 预算 env / 错误分类粒度） |
| `docs/plans/README.md` | 登记本 plan 为「进行中」 |

## Reused

| 复用 | 路径 |
|---|---|
| Adapter + LLM 调用 | `app/llm/adapter.py:LLMAdapter.generate` —— P6 已收敛 |
| Prompt 6 段结构 | `app/agent/prompts/sql_prompts.py` —— P7 已就绪 |
| Trace sdk | `app/infra/trace/sdk.py:Tracer` —— P7 已加 `add_prompt_version` |
| ErrorEnvelope 错误分类 | `app/models/contracts.py:ErrorDetail(kind)` —— P9 Reliability 待做，P8 只消费不重写 |
| Retry feedback loop | `sql_graph.py:_generate_sql` 现有 prev_sql + error 拼接逻辑 —— P8 改结构化，不重写 |
| FAQ fewshot | `app/infra/memory/` —— 已存在 `search_faq`，fewshot 来源 |
| Plan 失败分类 | `sql_graph.py:_evaluate` 现有 kind ∈ {timeout,connection,permission} 不重试 —— 保留 |
| `RetryCounter` 模式 | `sql_graph.py:SQLAgentState.retry_counters` dict —— P8 增 `MAX_SQL_REPAIR_RETRIES` 常量替代硬编码 |

## Verification

```bash
# 单测：DiagnosePolicy + 预算 env + Tracer.add_decision + RepairContext 注入
pytest tests/contracts/test_diagnose_policy.py -v
pytest tests/contracts/test_max_sql_repair_retries_env.py -v
pytest tests/contracts/test_tracer_decision.py -v
pytest tests/contracts/test_prompt_repair_context.py -v

# Goal test：执行闭环（Evaluate→Diagnose→Route）
pytest tests/graphs/test_execution_agent_loop.py -v

# 回归基线（以 master a6e9246 实测为准，预期 ~683 passed + 新增 ≥ 15；执行时重跑校准）
pytest tests/contracts/ tests/smoke/ tests/graphs/ --tb=line -q
```

**冒烟矩阵**：
1. 6 种 error_kind → 决策路径全覆盖（DiagnosePolicy 纯确定性单测）
2. env 真生效（monkeypatch 钉）且默认值 2/1 生效，先 grep 修 expectation 3→2
3. RepairContext 注入后 prompt 含「original_requirement / plan / SQL / error / kind / validation_result / retry_count / hint」（7 要素，schema 仅引用）
4. 4 个 goal test 全过：repair 成功 / replan 触发 / timeout 不重试 / budget 耗尽 clarify（走 _route_after_diagnose）
5. Decision trace 记录到 tracer._decisions list（含 EvaluateResult 与 DiagnoseDecision 双 span）

## Explicitly NOT doing

| 不做 | 理由 |
|---|---|
| 改 LLM Adapter | P6 已 PASS |
| 改 6 段 prompt 结构 | P7 已 PASS；P8 只在 prompt 末尾拼接 repair_ctx（含 7 要素，schema 仅引用），不动 6 段本体 |
| LLM Diagnose | P8 纯确定性 `DiagnosePolicy`，不做 LLM 策略；符合 Deterministic where correctness matters，留待 Evaluation 后再议（拍板） |
| 上 Langfuse | P13 范围；add_decision 本地记录 |
| 改 ErrorEnvelope 分类 | P9 Reliability 范围；P8 消费 ErrorDetail.kind |
| 改 Execution Loop 整体拓扑（如并发/并行节点） | P8 是「decision 化」不是「重写」；仅 Evaluate→Diagnose→Route 显式拆分 |
| 改 Requirement / Report Agent | P8 只动 SQL Execution 子图 |
| 引入新的 state 字段破坏 backward compat | RepairContext 通过 state dict 透传，向后兼容 |
| 改 LangGraph graph 拓扑大改（如加 parallel branch） | P8 仅 Evaluate 后新增 diagnose 单节点与 _route_after_diagnose，不做并行分支 |
| 新建 `execution_agent/` 子包 | 现有 `sql_graph.py` 扩即可，避免 Forbidden Pattern 新建通用文件夹 |

## TDD Tasks

### T1 DiagnoseDecision 数据类 + DiagnosePolicy 纯确定性策略
- [ ] Step1 `test_diagnose_policy.py` 红 → 绿（6 种 error_kind 决策路径全覆盖，纯确定性，无 LLM）
- [ ] Step2 `sql_graph.py` 引入 `EvaluateResult` / `DiagnoseDecision` / `RepairContext` / `DiagnosePolicy`
- [ ] Step3 保留 `_evaluate` 为判定节点，新增 `_diagnose` 调 `DiagnosePolicy.decide()`，新增 `_route_after_diagnose`

### T2 预算 env-driven
- [ ] Step0 `grep -rn retry_counters backend/tests` 评估现有用例对 3 的依赖，修 expectation 3→2
- [ ] Step1 `test_max_sql_repair_retries_env.py` 红（默认 2/1 + env 真生效 + monkeypatch）
- [ ] Step2 `sql_graph.py` 引入 `MAX_SQL_REPAIR_RETRIES=2` / `MAX_PLAN_RETRIES=1` env 常量（后者为现有行为显式命名）

### T3 7 要素 RepairContext 注入 prompt
- [ ] Step1 `test_prompt_repair_context.py` 红（build_sql_generate_prompt 接受 repair_ctx 后含结构化 7 要素，schema 仅引用）
- [ ] Step2 `sql_prompts.py` 改 build 函数签名 + 段尾拼接逻辑
- [ ] Step3 `_generate_sql` / `_plan` 重试分支构造 `RepairContext` 并传入

### T4 Decision Trace
- [ ] Step1 `test_tracer_decision.py` 红（Tracer.add_decision 记录 + span 关联）
- [ ] Step2 `Tracer.add_decision` 新方法
- [ ] Step3 `_diagnose` 决策进 trace

### T5 GoalTest 执行闭环（Evaluate→Diagnose→Route）
- [ ] Step1 `test_execution_agent_loop.py` 4 个 goal test（repair / replan / fail / clarify）走新路由
- [ ] Step2 跑全套件回归（以实测基线为准，预期 ~683 + 新增）
- [ ] Step3 `docs/plans/p8-golden-before-after.md` 写 Before/After

## Why this design

P8 是「decision 化」不是「重写」：
- 现有 linear skeleton（plan → generate → validate → execute → evaluate）保留并显式拆为 Evaluate→Diagnose→Route（Evaluate=发生了什么 / Diagnose=为什么+怎么办 / Route=下一步走哪，路由键由 `DiagnoseDecision.action` 给——F2 拍板）
- 仅在 evaluate 后新增 diagnose 单节点与 `_route_after_diagnose`，把硬编码 if-else 升级为 `DiagnosePolicy` 结构化决策
- 6 段 prompt 结构（P7）保留不动；`repair_ctx`（7 要素）作为可选参数拼接在 prompt 末尾——同 P7 retry feedback hook；schema 不进 repair 段（F7 拍板）
- 预算 env-driven（3 行常量替换，`2/1` 显式命名）——最小改动
- Decision Trace 进 tracer 本地 list（EvaluateResult + DiagnoseDecision 双 span）——P13 Langfuse 时再落库

## 修订记录（2026-08-29 拍板）

- 拓扑：采纳 Evaluate→Diagnose→Route，_evaluate 保留为判定节点产出 EvaluateResult，_diagnose 消费结果调 DiagnosePolicy（非 wrapper）
- RepairContext：补 `original_requirement` / `validation_result` / `retry_count`；schema_context_ref / fewshot 字段落地后由 Post-review Fix 删除（F7 / F8 拍板）
- 预算：`2/1` 确认为契约值，`MAX_PLAN_RETRIES=1` 为现有行为显式命名；先 grep 评估再改 expectation
- LLM Diagnose：P8 不做，纯确定性；开关留后续 Evaluation 后再议
- 基线：`586`→以 `master a6e9246` 实测为准（实测 620 passed / 0 failed，含 P8 新增 36 例）
- golden 位置：`docs/plans/p8-golden-before-after.md` 与 P7 统一

## Post-review Fix（2026-08-29 落地后 review→fix）

落地后由独立 review agent 出 14 条 findings，按 P0-P3 排序全部修：

| 编号 | 类别 | 标题 | 修法 |
|---|---|---|---|
| F1 | correctness | `_plan` 重置 retry_counters → 单次分析最坏 4 次 SQL retry | `_plan` 入口 `counters = dict(state.get("retry_counters") or {}); counters.setdefault(...)` 保留 _diagnose 写入 |
| F2 | spec-deviation | `_route_after_diagnose` 按 execution_status 而非 DiagnoseDecision.action 路由 | 改读 `decision["action"]`，execution_status 退化兜底（P7 兼容） |
| F3 | correctness | SUCCESS pass-through 写 action="fail" → P14 Evaluation 切片污染 | DiagnoseDecision.action Literal 加 `"end"`；SUCCESS 路径写 action="end" |
| F4 | correctness | `_format_repair_ctx` f-string Pydantic model repr 污染 prompt | 删「当前查询计划」行（plan 进入 RepairContext 但不渲染） |
| F5 | drive-by | `_generate_sql` 末尾 compat path 不可达 | 删 dead code 段（与上面 if 块条件完全相同） |
| F6 | correctness | `_diagnose` 重复 `json.loads(raw)` 解析 | 入口一次性 `parsed_raw` 局部变量，clarify/fail 分支共用 |
| F7 | drive-by | RepairContext.schema_context_ref 字段设而不读 | 删字段 + caller 写入 |
| F8 | drive-by | FAQ 块在 prompt 重复注入（完整 + 截断） | repair 段 fewshot 留空，由 6 段 faq_block 提供 |
| F9 | drive-by | `_plan` 直接索引 `state["user_query"]`，total=False 后 KeyError 风险 | 改 `state.get("user_query", "")` |
| F10 | drive-by | EvaluateResult.status 枚举 6 值用 3 个 | 缩为 `Literal["SUCCESS", "FAILED", "VALIDATION_FAILED"]` |
| F11 | test-coverage | 测试名"seven elements"但漏字段断言 | 补 target_metric 断言 + 增 schema/fewshot 不泄漏反向断言 |
| F12 | drive-by | `_diagnose` 中 `not kind` 死分支 | 删 `not kind or`，保留 `kind == "other"` 单分支 |
| F13 | drive-by | AGENTS.md / agent-flow.md 未同步 diagnose 节点 | AGENTS.md sql_agent 路径加 `→diagnose→`；agent-flow.md §六 现状映射更新 P8 已落地 |
| F14 | spec-deviation | plan D5 伪代码 `node=` 但实现用 `name=` | plan 文字 + 代码统一 `name=` |
| F15 | spec-deviation | plan D1「Evaluate 不做路由决策」与 execution_status 路由模糊 | 明确 evaluate 写 execution_status 给父图契约，路由键由 action 驱动（F2 拍板） |

**回归**：622 passed / 0 failed / 5 warnings（3:30）。P8 增量 36 例（34 原有 + 2 新增 F1/F3 反向钉），零回归。

## Review-2（2026-08-29 user-side review）

User push 到 `acsRef/ReportAgent` 远端后做实际 diff review：F1-F14 / F15 全部 PASS，但**新发现 1 P1 + 2 测试强度**：

| 编号 | 类别 | 标题 | 修法 |
|---|---|---|---|
| R1 | correctness | `_evaluate()` 在 `validation_failed` 时仍读 `sql_result`，跨 attempt stale 污染；上一轮 timeout 的 sql_result 残留 + 本轮 validate 失败 → evaluate 消费旧 kind="timeout"，但 DiagnosePolicy 把 timeout 错误地走 retry_sql | `_evaluate()` 入口先看 `validation_failed`，直接走 VALIDATION_FAILED 路径，不读 sql_result；同时 `_generate_sql()` 生成新 SQL 时清 `sql_result/evaluate_result/error`（R3） |
| R2 | dead-code | `DiagnosePolicy.decide()` 在 `raw_empty or validation_failed` 分支里先 normalize kind={syntax,object,other}，再判断 timeout/connection/permission fail——后者永远进不去；叠加 R1 时会让 timeout 被 normalize 成 other 误判 retry | 简化：validation_failed / raw_empty 路径直接按 retry budget 走，不再二次 normalize；timeout/connection/permission fail 只留给 raw 路径 |
| R3 | state-hygiene | `_generate_sql()` 生成新 SQL 不清 `sql_result / evaluate_result / error`，旧 execution data 残留 | `_generate_sql()` 返回 dict 显式清三字段；validation_result 不清（caller 已用其构造 repair_ctx，validate 节点下一轮重写） |
| R-test-budget | test-strength | 之前 `_eval_and_diagnose` 只测局部函数；缺真实 `build_sql_graph().invoke()` 闭环 | 新增 `test_full_retry_lifecycle_respects_budget`——monkeypatch call_llm / validate_sql（**注意 patch `sql_graph` 模块本地引用**而非源模块，否则 import 已绑定不生效），mock plan 合法 JSON + generate 永远 invalid SQL，验证 SQL repair ≤ 3 / plan ≤ 1 / 最终 NEED_CLARIFICATION |
| R-test-graph | test-strength | 之前 `_route_after_diagnose` 只调函数本身；缺 wiring 后行为一致验证 | 新增 `test_compiled_graph_routes_by_diagnose_decision`——6 个分支（retry_sql/replan/end/fail/clarify + 兜底 execution_status）全覆盖 |
| R-test-stale | test-strength | 缺 R1 反向钉——stale sql_result 不会污染 validate fail 路径 | 新增 `test_evaluate_prioritizes_validation_over_stale_sql_result`——同时注入 stale sql_result(timeout) + 本轮 validation_result(valid=False)，断言 evaluate 走 VALIDATION_FAILED + kind="syntax"（不是 timeout），后续 diagnose action="retry_sql" |

**回归**：626 passed / 0 failed / 5 warnings。P8 增量 40 例（37 + 3 新增 R-test：37 = 34 原有 + 2 reverse-nail + 1 polish 拆分净增，3 来自 R-test-budget / R-test-graph / R-test-stale），零回归。

## Open questions

无。P9 Reliability 可接管 DiagnosePolicy 至 ErrorEnvelope 统一分类；P13 Langfuse 落库 `_decisions`；P14 Evaluation 按 action 切片统计（前提是 F3 已修，"end" vs "fail" 严格区分）。