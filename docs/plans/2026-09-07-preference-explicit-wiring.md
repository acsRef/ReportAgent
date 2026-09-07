# P16.5 Preference Explicit Wiring（简历盘点发现缺口，用户拍板修）

> 状态: 进行中
> 分支: `p16-5-preference-explicit-wiring`　|　commit 统一带 `+ plan: preference-explicit-wiring`

## Context（为什么做）

简历数据盘点（2026-09-07）发现：**偏好记忆卖点在真实 UI 链路不可达**——memory-architecture §五「Explicit user statement → stable_preference（active 直接存）」的实现 `manager.remember_explicit_preference`（backend/app/memory/manager.py:90）**在生产链没有调用者**（全仓 grep 证实；唯一消费者是 P16 测试直插）。

真实路径：用户在 UI 说「以后报告都用柱状图」→ 对话压缩 LLM 抽取（conversation.py:174）→ `remember_inferred_facts` → **candidate（按设计不召回）**；重申同句也不 promote（`UserMemory.save` 的 promote 条件 candidate→active，而写入恒 candidate）。后果：偏好永远不进 report 链，`chart_advisor` 默认 pie 不变——P16 W2/G4 全绿是靠测试直插 active 行绕过生产写入口。S2 截图（偏好生效）因此做不了。

修法（用户拍板：最小接线）：chat 入口图前短路，复用现成 `MemoryPolicy.extract_preference` 正则（已验证「以后都用柱状图」命中 pattern1）+ `remember_explicit_preference`。**不是新能力**——补 §五 契约已有、主链没接的线；P16 后 Memory 不再扩（本 plan 为唯一例外，用户明确批准）。

## Design

**D1. manager 薄封装**（backend/app/memory/manager.py）：`extract_explicit_preference(text: str) -> MemoryEntry | None`（sync，委托 `_policy.extract_preference`）——main.py 不直接碰 infra.memory.policy（分层干净，manager 是 domain 入口）。

**D2. main.py 图前短路**（`_chat_requirement_analysis`，main.py:623）：
- `graph.ainvoke` 之前：`entry = extract_explicit_preference(request.user_query)`；命中 → `await remember_explicit_preference(user["id"], request.user_query)`（try/except 失败不阻塞）→ SSE：`phase: idle` + `report: {answer: {text: "好的，已记住：<原文>"}}` → return（**不 invoke requirement 图**——省一次 LLM + 不产 noise draft；复用 chitchat 渲染路径，前端零改动）。
- 顺序：session 创建/save_message 之后、trace 建立之后、图之前。mode=new/supplement 都生效；mode=adjust（分析续跑）不短路。
- 幂等性：重申同句 → save 走 existing→access_count+1 分支（active 保持）✅；与 active 冲突 → `remember_explicit_preference` 内 supersede ✅。

**D3. 测试**：
- `backend/tests/api/test_explicit_preference.py`（offline，monkeypatch 图模式同 test_requirement_stream.py `_patch_stream`）：① 偏好句 → **图不被 invoke**（patch `_Boom()` graph，无异常即证明短路）+ SSE 事件 = phase idle + report.text；② 非偏好分析句 → 图正常 invoke（既有行为不破）；③ remember 抛异常 → 轻响应仍出（不阻塞）。
- `backend/tests/persistence/test_preference_explicit_wiring.py`（真 PG）：偏好句走 handler → `semantic_entry` 出现 active+high 的 stable_preference 行（user=admin）+ 随后 `report_plan_analysis` 档 `ContextRuntime.build` 召回该偏好（闭环断言：UI 语句 → 记忆 → 召回）。

## Files to change

- `backend/app/memory/manager.py`（+extract_explicit_preference 薄封装）
- `backend/app/main.py`（_chat_requirement_analysis 图前 ~12 行）
- `backend/tests/api/test_explicit_preference.py`（新）+ `backend/tests/persistence/test_preference_explicit_wiring.py`（新）

## Reused existing utilities

- `MemoryPolicy.extract_preference`（infra/memory/policy.py:45，正则已验证命中）；`remember_explicit_preference`（manager.py:90）
- chitchat 终态模板（main.py:682-695：phase idle + report.text 轻响应——**逐字照抄渲染形态**）
- api 测试 mock 模式（tests/api/test_requirement_stream.py:21-27 `_patch_stream`）与 persistence 惯例（sync `_run` + init_pool）
- P16 W2 的「直插 active 断言召回」模式（test_report_chart_preference_behavior.py）

## Verification

```bash
cd backend && pytest tests/api/test_explicit_preference.py tests/persistence/test_preference_explicit_wiring.py -v
cd backend && pytest -q            # 全量零回归（当前基线 1176 passed/1 skipped）
# UI 手工门（S2 截图前置）：前端对话「以后报告都用柱状图」→ 回复已记住 → 再问
# 「2024年各区域销售额」→ 报告 chart = bar（此前为 pie）
```

## Explicitly NOT doing

- 不做「偏好+分析混合句」双重路由（如「以后按月看，顺便看下华东」——本体任务仍走图，偏好抽取留给 LLM→candidate，不阻塞；此简化记入落地偏差）
- 不改 LLM 抽取 candidate 路径（V1 简化保持）；不接 temporary_preference 显式检测（Temporal 词场景不改）
- 不碰前端；不进 report_plan prompt；不扩 Memory（P16 后封版边界）
- 不做「偏好写入成功/失败的 UI 差异」（统一轻响应）

## Task 分解

- T0 plan 落库 + README 登记 + 开分支
- T1 D1+D2 接线（manager helper + main.py 短路）
- T2 D3 测试（api 3 例 + persistence 1 例闭环）
- T3 全量回归 + docs（CLAUDE.md §6 补一行 P16.5）
