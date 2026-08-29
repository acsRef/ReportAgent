# P4c Task 2 — 主链行为观察

## 落地
- `tests/graphs/test_context_runtime_main_chain.py`：3 个钉子 + 1 个 autouse fixture（noop memory recall，防 LegacyFallbackPolicy 在无 DATABASE_URL 时 get_pool 报错）

## 钉子结果（11/11 pass）

| 钉子 | 状态 |
|---|---|
| `test_requirement_agent_entry_has_conversation_context` | ✅ bundle.conversation_context 含 `<L1>history</L1>`，agent_policy=`"requirement"` |
| `test_confirmed_execution_agent_entry_has_conversation_context` | ✅ bundle.conversation_context 含 `<L2>confirmed-context</L2>`，agent_policy=`"execution"` |
| `test_selective_policy_injects_recall_into_assembled` | ✅ SelectiveRecallPolicy + 注入 preference 后，assembled_context 含 `"user prefers bar charts"` raw_text |
| `test_p4c_graph_caller_integration`（已有 8 子测试） | ✅ 8/8 PASS |

## 关键发现：ContextPolicyResolver strict prefix

`app.context.policy.ContextPolicyResolver.resolve(agent_name: str)` 用 `startswith` 匹配，规则：

```python
"requirement_*"            → REQUIREMENT
"confirmed_execution_*"    → EXECUTION   # 注意下划线后缀
"sql_*" / "_generate_sql*" / "data_*" → EXECUTION
"report_*"                 → REPORT
其他                         → REQUIREMENT（保守 fallback）
```

**初版 caller 错传** `agent="confirmed_execution"`（缺下划线后缀） → resolver fallback 到 REQUIREMENT → 触 `MemoryManager.recall_structured()`（在 EXECUTION 上不期望发生）→ 错分配 agent_policy 给下游 selective policy。

**修复**（commit 9fefa44 内）：caller 改用 `agent="confirmed_execution_sql_agent"`，符合 `startswith("confirmed_execution_")` 规则，下游接收到 `agent_policy=execution`，selective §三分流生效（query 流可触发）。

## 主链行为影响

- Requirement Agent 入口（_requirement_parse）：ContextRuntime 全链路接入 → bundle 含 conversation_context + assembled_context → 透传至 `_call_llm_for_parse` prompt 注入
- Confirmed Execution Agent 入口（_confirmed_sql_agent）：ContextRuntime → bundle `assembled_context` 注入 `sql_graph.ainvoke(...)` state → `_plan` 与 `_generate_sql` 从 `state["assembled_context"]` 优先读取 → fallback `state["conversation_context"]`

## 持久化冒烟
- DATABASE_URL 未设：LegacyFallbackPolicy + semantic_memory/query_memory autouse noop fixture 让 ContextRuntime.build() 跑通无 DB 依赖
- DATABSE_URL 设后：caller 真实路径走 MemoryManager.recall_structured() + UserMemory/QueryMemory 持久化层（P4b 已落）

## 风险评估

| 风险 | 状态 |
|---|---|
| 主链回归（钉子已防） | ✅ 3 smoke 钉子 |
| selective policy 行为漂移 | ⚠️ 仍需 Task 3 验证（selective 收益矩阵） |
| assembler 拼接行为漂移 | ⚠️ 仍需 Task 4（Filter/Budget 真实装） |
| golden 用例 SQL 生成受影响 | ⚠️ 仍需 Task 5（before/after 对比） |

## 验收
- Task 2 Step 1-3 ✅（钉子先红后绿）
- Step 4 manual SSE smoke：⏭ 按 CLAUDE.md §15 P12 前手动门，留 P12 时一起验
- Step 5 ✅（本文档）
- Step 6 待 commit
