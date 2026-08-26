# State Contract（五块状态契约）

> 状态: 冻结（P1，2026-08-26）— 目标契约。当前实现差距见「现状映射」节与各 Phase plan。
> 上游: [2026-08-25-refactor-master-freeze.md](../plans/2026-08-25-refactor-master-freeze.md) §五。

## 一、State 拆分（冻结）

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

## 二、字段所有权规则

| 规则 | 内容 |
|---|---|
| immutable | `original_query` 一经写入不得修改；所有改写只落 `current_query` |
| 单一写者 | 每个字段只有一个节点/阶段负责写入；跨块读取必须显式声明依赖 |
| 生命周期 | RequestState 随请求生灭；RequirementState 随 session 草稿生灭（draft lock 保护）；ExecutionState / ReportState 随一次 confirm/adjust 生灭并落 append-only ReportVersion；RuntimeState 纯进程内 |
| 边界传递 | 块与块之间通过显式字段交接，不允许一个节点整包重写他块状态 |

## 三、现状映射（截至 P1）

| 契约要素 | 现状 | 差距归属 Phase |
|---|---|---|
| ExecutionState 子集 | `backend/app/agent/sql_graph.py` `SQLAgentState`（schema_context / query_plan / generated_sql / validation_result / query_result / execution_status / error / retry_counters） | P3 归位改名 |
| RequirementState 子集 | `requirement_analysis_graph.py` 内部 state + `models/requirement.py` `RequirementCard` + draft 持久化（`agent.requirement_draft`） | P3 归位 |
| RequestState 子集 | main.py 入口构造（session_id / user_id / original_query / current_query 分散在 input_data 与 session_manager） | P3 收拢 |
| ReportState 子集 | confirmed_execution_graph 的 report 节点产物 + `services/report_version_service.py` append-only 落库 | P3/P10 分工：state 归 P3，Domain Object 归 P10 |
| RuntimeState 子集 | trace_id 已贯穿各图；memory_context 在 legacy 图与 context.py；tool/mcp_calls 未结构化 | P3 建 `RuntimeState` |
| legacy 大 State | `backend/app/legacy/agents/parent_graph.py` `AgentState`（20+ 字段单块）——不迁移，随 P15 删除 | — |

**checkpoint 兼容性风险（P3 plan 必须处理）**：非 dev 环境用 `AsyncPostgresSaver`，State 结构变更影响已序列化 checkpoint 的反序列化；P3 需给出兼容或废弃策略。
