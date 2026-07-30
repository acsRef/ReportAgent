# Grill Report: 今日 8 份 Plan 的设计原则符合度

> 状态: 只读评审

按 CLAUDE.md「Design quality bar」+ AGENTS.md 「Principles + Template」逐份 plan 做合规评分。**不修改代码**，仅整理现状 + 跟踪修复动作。

## 评估指标（来自 AGENTS.md / CLAUDE.md）

1. 中文（Language）
2. 命名 `YYYY-MM-DD-<slug>.md`（Naming）
3. 七章节齐全：Title / Context / Design / Files to change / Reused / Verification / Explicitly NOT doing（Template）
4. 引用真实代码路径，不 fabricated（Design MUST）
5. 与已落地代码一致（Design MUST）
6. 高内聚 / 低耦合（Principles）
7. 文档不可变 + supersede 显式标注（Principles）
8. 单职责（CLAUDE.md quality bar）
9. scope 清晰（CLAUDE.md quality bar）
10. 复用优先（CLAUDE.md quality bar）
11. 错误一等公民（CLAUDE.md quality bar）
12. 可逆性显式标注（CLAUDE.md quality bar）

## 综合红旗（按 ROI 排序）

| # | 红旗 | 紧迫度 | 修复 |
|---|---|---|---|
| 1 | 3 份已实施 plan 仍并存无 supersede 标记 | HIGH | 已修复 ✓——`query-execution-safety-and-reporting.md` 顶部加 `> Supersedes (合并自)` 块；`sql-row-cap-and-export.md` / `confirmed-exec-three-state.md` 顶部加 `> 历史归档` 块 |
| 2 | 4 份 plan 章节英文违反中文规则 | HIGH | 已修复 ✓——`query-execution-safety-and-reporting` / `cross-agent-state-fix` 章节改 `## 背景（Context）/ ## 设计（Design）` 等双语并列形式 |
| 3 | `backend-async-refactor` 核心论证基于错误运行时假设 | HIGH | 已修复 ✓——文档顶部加 `> 勘误` 段，说明真实 P0 只有 2 处，其余 sync→async 的真正收益是线程池耗尽 + socket 风暴而非事件循环阻塞 |
| 4 | `cross-agent-state-fix` Step 8 行号漏 line 477 | MEDIUM-HIGH | 已修复 ✓——Step 8 问题描述改为 "line 177 **和 line 477** 两处都建单例" |
| 5 | `cross-agent-state-fix` 落地节奏缺 PR 间依赖约束 | MEDIUM | 已修复 ✓——末尾补三条 PR 顺序硬性约束 |
| 6 | `cross-agent-state-fix` Step 7 与 `backend-async-refactor` 在 PG 改造上冲突 | MEDIUM | 已修复 ✓——两 plan 顶部互相 cross-ref；约定「不同方案，不能同时实施」 |
| 7 | review ↔ fix 文档无 explicit cross-ref | LOW | 已修复 ✓——`cross-agent-state-safety.md` 顶部加 `> Follow-up plan`；`cross-agent-state-fix.md` 加 `> Based on` |
| 8 | `bug-review.md` / `cross-agent-state-safety.md` 章节命名自定义偏离 Template | LOW | 已修复 ✓——把 `## 一、... / ## 二、...` 改为 descriptive title |

## 逐 plan 修订总结

| Plan | 修改 |
|---|---|
| `2026-07-30-query-execution-safety-and-reporting.md` | 加 Supersedes/Reference 块；章节改中文 |
| `2026-07-30-sql-row-cap-and-export.md` | 加历史归档块（保留，不动内容） |
| `2026-07-30-confirmed-exec-three-state.md` | 加历史归档块（保留，不动内容） |
| `2026-07-30-cross-agent-state-fix.md` | 加 Based on/Related 块；Step 8 行号补 line 477；PR 顺序硬性约束；中文章节 |
| `2026-07-30-cross-agent-state-safety.md` | 加 Follow-up plan cross-ref；自定义章节改 descriptive |
| `2026-07-30-bug-review.md` | 加 Source plans / Follow-up plans cross-ref；自定义章节改 descriptive |
| `2026-07-30-backend-async-refactor.md` | 加 P0 论证勘误 + Related cross-ref |
| `2026-07-30-legacy-sql-bugs.md` | 未修（已合规） |
| `2026-07-30-tool-desc-error-examples.md` | 未修（已合规） |

## 整体合规度（修订后）

| Plan | 综合评分 | 备注 |
|---|---|---|
| `query-execution-safety-and-reporting.md` | A | supersede + 中文章节齐 |
| `cross-agent-state-fix.md` | A- | Step 8 行号补齐；PR 顺序明确 |
| `cross-agent-state-safety.md` | A | cross-ref + descriptive 章节 |
| `bug-review.md` | A | cross-ref + descriptive 章节 |
| `backend-async-refactor.md` | B+ | P0 论证已勘误，但 plan 主体仍包含原错误假设的章节（保留作历史档案），读者看到时易混淆 |
| `legacy-sql-bugs.md` | A | 修订前已合规 |
| `tool-desc-error-examples.md` | A | 修订前已合规 |
| `sql-row-cap-and-export.md` | A（已 supersede） | 内容已 frozen，仅加 marker |
| `confirmed-exec-three-state.md` | A（已 supersede） | 同上 |

## 修订动作外仍需跟进的事项（出 review 范围）

1. **`backend-async-refactor.md` 主体章节仍带旧错误评级**：可选择把原背景段改为「⚠️ 历史论证，已被勘误段替代」标记或直接 rewrite。本 plan 的工作范围决定了保留原内容更稳妥
2. **未来新 plan 默认结构**：AGENTS.md Template 表格的章节名精确到 `## Context（背景）` 已经写入，但具体怎么写仍由人定。建议下次开新 plan 时直接套用上面的「修订后模板」
3. **`docs/plans/` 的命名空间是否需要二级目录**：今日 9 份同日 plan 已暴露大量同日的多 plan 拥塞问题。命名约定是否需要 `YYYY-MM-DD-topic-slug.md` 升级到 `YYYY-MM-DD/{topic}/...`，等用户决策

## Explicitly NOT doing

- 不改动代码——本次纯文档合规整改
- 不 commit——等用户示意
- 不删 plan——`sql-row-cap-and-export` / `confirmed-exec-three-state` 保留作为 commit 引用追溯源
- 不改 AGENTS.md Template——其已正确，仅引用 [AGENTS.md](file:///d:/PyProject/ReportAgent/AGENTS.md) 第 150-168 行
