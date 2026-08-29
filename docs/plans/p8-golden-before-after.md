# P8 Golden Set Before/After After

> 状态: 已完成（含 Post-review Fix：14 findings 全修 + 2 反向钉新增；622 passed / 0 failed）
> 上游: [2026-08-29-p8-execution-agent-loop.md](2026-08-29-p8-execution-agent-loop.md) §D6 + [2026-08-25-refactor-master-freeze.md](../2026-08-25-refactor-master-freeze.md) §四/§十一

## Context

P8 是「decision 化」不是「重写」：保留 `plan → generate → validate → execute → evaluate` 线性骨架，仅将 `evaluate` 内硬编码 `if-else` 拆为显式 `Evaluate → Diagnose → Route` 三段（Evaluate=发生了什么 / Diagnose=为什么+怎么办 / Route=下一步走哪），使 Agent 决策可观察、可预算、可回灌。

## Before（P8 起点，commit `a6e9246`）

- `_evaluate` 单节点既做结果判定又做路由决策：4 分支硬编码 `SQL_SYNTAX_ERROR / SCHEMA_ERROR / FAILED / SUCCESS / NEED_CLARIFICATION`
- 预算硬编码 `sql_retries < 3` / `plan < 1`（与伞形 §十一 SQL repair 2 冲突）
- 无 `_diagnose` 节点，决策依据未结构化（仅 `execution_status` 字符串）
- 上下文回灌仅 `prev_sql + error` 字符串拼接（6 要素不全，缺 original_requirement / schema_context_ref / validation_result / retry_count / hint / fewshot）
- 失败分类仅 `timeout/connection/permission` 不重试，其余 `syntax/object/other` 共用同一路径
- 决策仅靠 `@traced_node` span，无 `DiagnoseDecision` 结构化记录

## After（P8 落地后）

- `_evaluate` 保留为确定性判定节点：产出 `EvaluateResult{status,kind,error,validation_result}`（发生了什么）；仍写 `execution_status` 给父图契约
- 新增 `_diagnose` 决策节点：消费 `EvaluateResult + retry_counters + error_history`，调 `DiagnosePolicy.decide()` 产出 `DiagnoseDecision{action,reason,error_kind,recoverable,retry_target,hint,confidence}`（为什么/怎么办）；SUCCESS pass-through 写 action="end" 与"fail"严格区分
- 新增 `_route_after_diagnose`：按 `DiagnoseDecision.action` 路由 `generate_sql / plan / build_output / END`，execution_status 退化兜底（P7 兼容）
- `DiagnosePolicy` 纯确定性规则：`syntax/object/other → retry_sql → replan → clarify`，`timeout/connection/permission → fail`；P8 不做 LLM 策略
- 预算 env-driven：`MAX_SQL_REPAIR_RETRIES=2` / `MAX_PLAN_RETRIES=1`（后者为现有行为显式命名），`_get_max_*()` 运行时读 env，`monkeypatch.delenv/setenv` 可钉
- `RepairContext` 结构化 7 要素回灌：`original_requirement / plan / target_metric / prev_sql / error / error_kind / validation_result / retry_count / hint`，经 `build_sql_generate_prompt(repair_ctx=)` 与 `build_sql_plan_prompt(repair_ctx=)` 拼到 6 段末尾（schema 由 6 段注入，repair 段不重复；F7/F8 拍板）
- `Tracer.add_decision(name, **fields)` 本地 `_decisions` 记录，双 span（Evaluate + Diagnose）可追踪，P13 再落库
- `_plan` 入口保留 `retry_counters`（不再无条件 reset；F1 真 bug 修复后 `MAX_SQL_REPAIR_RETRIES=2` 契约生效）

## 等价性论证

- 成功路径零行为变更：`SUCCESS → build_output` 仍直通（action="end" 与旧 "fail" 字面差异仅在 trace 语义）
- 失败路径语义收敛：原 `sql <3 → retry` / `plan <1 → replan` 逻辑平移至 `DiagnosePolicy`，仅默认值 `3→2` 收敛至契约
- Prompt 结构不动：6 段本体不变，`RepairContext` 仅末尾追加，不重排 task_contract / tool_policy
- 预算可回滚：`MAX_SQL_REPAIR_RETRIES=3` 仍可通过 env 复现旧行为

## 测试钉

| 测试 | 覆盖 |
|---|---|
| `test_diagnose_policy.py` 12 例 | 6 种 kind 决策路径全覆盖（纯确定性） |
| `test_max_sql_repair_retries_env.py` 5 例 | 默认 2/1 + env 真生效 + delenv + 非法回退 |
| `test_prompt_repair_context.py` 5 例 | `build_sql_*_prompt` 7 要素拼接含全字段 + 无 ctx 时不污染 + schema/fewshot 不泄漏 |
| `test_tracer_decision.py` 6 例（含新增 F3 反向钉） | `add_decision` 本地记录 + span 关联 + diagnose 节点落迹 + SUCCESS action="end" + fail action="fail" |
| `test_execution_agent_loop.py` 8 例（含新增 F1 反向钉） | 4 goal：repair / replan / timeout fail / budget clarify + success action="end" / validation fail / connection + replan 不重置计数 |

合计 **36 例**（34 原有 + 2 新增反向钉）。

## 回归基线

```bash
cd backend
pytest tests/contracts/test_diagnose_policy.py tests/contracts/test_max_sql_repair_retries_env.py tests/contracts/test_prompt_repair_context.py tests/contracts/test_tracer_decision.py tests/graphs/test_execution_agent_loop.py --tb=short -q
# 36 passed, 2 warnings

pytest tests/smoke tests/contracts tests/graphs --tb=line -q
# 622 passed, 0 failed, 5 warnings (3:30)
```

与 P7 起点 `a6e9246` 对比：`+36` 测试（12+5+5+6+8），零回归失败。`586 → 622` 增量即 P8 新增。

## Post-review Fix（2026-08-29）

落地后由独立 review agent 出 14 findings：1 真 bug（F1 `_plan` 重置 retry_counters → 单次分析最坏 4 次 SQL retry，最严重）+ 5 spec-deviation/correctness（F2/F3/F4/F6/F15）+ 3 dead-code/重复（F5/F8 注释、解析重复）+ 4 文档/契约对齐（F7/F9/F10/F11/F12/F13/F14）。**全部修**；详情见 plan Post-review Fix 章节。

## Commit 序列

- `p8-execution-agent-loop` 分支：`sql_graph.py` 扩 Evaluate→Diagnose→Route + `RepairContext` + `DiagnosePolicy` + env 预算 + `sdk.py` add_decision + `sql_prompts.py` repair_ctx
- 落地后 review-fix（单 commit）14 findings + 2 反向钉
- 真端到端 Golden Set：留 P12 手动门（`REPORTAGENT_E2E=1 pytest tests/e2e/test_full_flow.py -s` + `evaluation/runner.py`）

## 后续 Phase 衔接

- **P9 Reliability**：`DiagnosePolicy` 可收编至 `reliability/errors.py` ErrorEnvelope 统一分类
- **P13 Langfuse**：`Tracer._decisions` 落库至 span attribute，按 `trace_id` JOIN
- **P14 Evaluation**：按 `DiagnoseDecision.action` 切片统计 repair 成功率 / clarify 准确率（F3 拍板："end" 与 "fail" 严格区分，SUCCESS 不再污染 fail 切片）
