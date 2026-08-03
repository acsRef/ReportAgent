> 状态: 已完成（v2 修订落地并验证：graphs 24 passed / 全量 158 passed；v1 曾标记已完成但 review 发现 3 处问题，已由「修订 v2」节修正）

# SQL Prompt 规则增强（JOIN / 时间 / 数组 / 空结果话术）

## Context（背景）

对照 10 模块 Prompt 集审计发现，`sql_graph._generate_sql` 的规则块只有一句泛泛的
JOIN 指引（「JOIN 条件使用外键关联」），缺少：

- JOIN 专项约束（LEFT JOIN 优先、ON 归属、GROUP BY 完整性、多表分层等）
- 时间维度拆分规则（相对时间 + 绝对时间混用时如何分别换算）
- 数组类型适配（`@>` vs `LIKE '%标签%'`）
- 空结果话术（EMPITY 时没有「放宽筛选条件」引导）

已确认：本次只做这 4 类 prompt/话术增强，排除 HTTP 接口 Agent（模块七）与
RAG 知识库（模块十）。时间/数组规则按本库真实 schema 适配（无 TIMESTAMP、无数组列）。

## Design（设计）

### A. `_generate_sql` 规则块扩展（`backend/app/agent/sql_graph.py`）

新增模块常量 `_SQL_GENERATION_RULES`，追加进 `_generate_sql` 的 prompt 规则块。
内容按三类组织：

1. **JOIN 8 条**（适配真实外键）：
   - 多表关联优先 `LEFT JOIN`，禁止 `RIGHT JOIN`；FROM 后第一张表即主表
   - JOIN 关联条件一律写 `ON`，禁止下沉到 WHERE
   - 维度表过滤条件写 `ON`，主表过滤写 `WHERE`
   - 有聚合时 `GROUP BY` 必须含所有非聚合查询列
   - 关联超过 3 张表时拆成两层子查询（先各子表聚合，再外层 JOIN）
   - 输出 SQL 顶部附注释说明关联逻辑（主键/外键链路）
   - 明细/非聚合查询默认追加 `LIMIT 200`
   - 列引用必须来自「可用表结构」中真实存在的列，不臆造
2. **时间维度拆分**（适配 `dim_date` 结构，无 TIMESTAMP 列）：
   - 时间过滤一律经 `date_id` 外键关联 `dim_date`，落到 `full_date` 区间
   - 相对时间（今年/上月/近 N 天）与绝对时间（具体日期）统一换算为
     `[start, end)` 左闭右开区间
   - 同时含相对+绝对时间时，用两个带别名的时间子查询各自算区间后 JOIN，
     **禁止同一 WHERE 混写两种时间逻辑**
3. **数组类型**（防御性，schema 当前无数组列）：
   - 若目标列为数组类型（ARRAY），必须用 `@> ARRAY['标签']` 匹配，
     禁止 `LIKE '%标签%'`；当前表结构尚无数组列，遇到请先确认列类型

### B. `_PLAN_TABLE_HINTS` 补充外键链路

`_PLAN_TABLE_HINTS`（`sql_graph.py` L96-98）追加各事实表到维度表的外键映射，
供 plan 与 generate_sql 两处 prompt 使用：

```
事实表 → 维度表外键:
- fact_sales: date_id→dim_date, region_id→dim_region, product_id→dim_product, customer_id→dim_customer
- fact_returns: return_date_id→dim_date, product_id→dim_product, sale_id→fact_sales
- fact_inventory: date_id→dim_date, product_id→dim_product, warehouse_id→dim_warehouse
- fact_attendance: date_id→dim_date, employee_id→dim_employee
```

### C. 空结果话术（`backend/app/agent/confirmed_execution_graph.py`）

`_confirmed_report_agent` 的 EMPTY 分支（L305-309）当前 `insight_text=None`。
改为写入友好提示，随 report SSE 下发并落入 `agent.report_version` 历史：

```
insight_text = "未找到匹配数据。你可以尝试放宽筛选条件，比如扩大时间范围或调整关键词。"
```

前端 `ReportPaper` 已分别渲染空态 band（「未找到匹配记录」）与 核心发现 band
（`answer.insight`），无需改动前端。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/agent/sql_graph.py` | 新增 `_SQL_GENERATION_RULES` 常量并接入 `_generate_sql` prompt；`_PLAN_TABLE_HINTS` 补外键链路 |
| `backend/app/agent/confirmed_execution_graph.py` | `_confirmed_report_agent` EMPTY 分支写入友好 `insight_text` |
| `backend/tests/graphs/test_sql_generation.py` | 新增 prompt 内容断言：含 LEFT JOIN / GROUP BY / LIMIT 200 / 子查询 / `@>` / 外键链路关键词 |
| `backend/tests/graphs/test_confirmed_report_agent.py` | EMPTY 测试补 `insight_text` 断言 |
| `docs/plans/2026-08-03-sql-prompt-rules.md` | 本文件 |
| `docs/plans/README.md` | 登记进行中表 |

## Reused existing utilities（复用工具）

- `extract_sql`（`app/utils/text.py`）：注释在首条语句 `;` 之前会被保留，
  prompt 的「附注释」要求可直接生效，无需改动
- 测试手法复用 `test_sql_graph_output.py` L57-80 的 `fake_call_llm` 捕获 prompt 模式

## Verification（验证）

- `cd backend && pytest -m graphs tests/graphs/test_sql_generation.py tests/graphs/test_confirmed_report_agent.py`
- `cd backend && pytest -m graphs`
- `cd backend && pytest`（全量离线）
- 手动：对含混时间的查询（如「对比 2024-01 与上月」）产出的 SQL 应含两个时间子查询 + JOIN

## Explicitly NOT doing（不做事项）

- HTTP 接口取数 Agent（模块七）与 RAG 知识库（模块十）——留作后续独立 plan
- 不改 `_plan` 的 `time_range` 判定逻辑（时间规则只在生成 SQL 层约束）
- 不改 `parent_graph.py` legacy 报告路径（EMPTY 分支只在 v2 confirmed 流落地）
- 不改前端

---

## 修订 v2（review 发现的问题与修正）

v1 落地后经对照**真实 schema 与运行代码** review，schema 事实（外键链路 / dim_date
无 month / 无数组列）全部准确，但发现 3 处真问题 + 1 处次要语义点。v2 修正如下。

### 问题 1（功能缺口）：外键链路没进 `_generate_sql`，与 v1 声明矛盾

v1 声称外键链路「供 plan 与 generate_sql 两处 prompt 使用」，但 `_PLAN_TABLE_HINTS`
只拼进了 `_plan`（sql_graph:300），`_generate_sql` 的 prompt（378–399）**没有**外键
映射——真正写 JOIN 的节点看不到 `fact_sales.date_id→dim_date` 这类对照，只能从列名猜。
且 v1 测试 `test_generate_sql_prompt_contains_fk_hints` 只断言常量 `_PLAN_TABLE_HINTS`，
没断言它进了 `_generate_sql` 的 prompt，掩盖了缺口。

**修正**：
- 把外键链路抽成独立常量 `_FK_CHAIN_HINTS`（单一来源），`_PLAN_TABLE_HINTS` = 表速查 + `_FK_CHAIN_HINTS`；
- `_generate_sql` 的 prompt 显式拼入 `_FK_CHAIN_HINTS`；
- 测试改为对**捕获到的 `_generate_sql` prompt** 断言全部外键映射（通用、不钉常量）。

### 问题 2（功能缺口）：相对时间换算缺「当前日期」注入，规则落不了地

时间规则要求把「今年/上月/近 N 天」换算成 `[start, end)` 具体区间，但 `_plan` /
`_generate_sql` 的 prompt **都没注入当前日期**——模型不知道今天几号，相对时间无法
正确换算（只能凭训练数据猜出过时日期）。时间拆分规则因此无法可靠生效。

**修正**：`_plan` 与 `_generate_sql` 的 prompt 都注入 `当前日期: {date.today().isoformat()}`
（`from datetime import date`），给相对时间一个换算基准。测试断言 prompt 含
**运行当天**的 ISO 日期（动态计算，不硬编码，保证通用性）。

### 问题 3（v1 声明错误 + 误切风险）：「顶部附注释」规则与 `extract_sql` 冲突

v1「复用工具」节声称「extract_sql 注释会被保留…无需改动」——**错误**。`extract_sql`
（text.py:31-33）从第一个 `select` 切片，会丢弃 SELECT 前的 `--` 注释；若注释恰好含
「select」字样还会在注释中间误切出残缺 SQL。「附注释」规则白做且有隐患。

**修正**：**移除**「SQL 顶部用注释说明关联逻辑」这条规则（注释对执行无意义，
`execute_sql` 还要再包一层 CTE，保留无价值）。`_SQL_GENERATION_RULES` 的 JOIN 规则
由 9 条收敛为 8 条。同步更新 `test_generate_sql_prompt_contains_join_rules`（去掉
「说明关联逻辑」断言）。**不改 `extract_sql`**（它在关键路径、已被充分测试，不动它最稳）。

### 问题 4（次要语义，仅记录不改码）：明细 `LIMIT 200` 使行数/截断标记失真

`execute_sql` 是 `WITH src AS ({sql}) … count(*) … LIMIT 5001`。明细 SQL 自带
`LIMIT 200` 时，`count(*)` 数的是被 200 截断后的 src → 报告显示「共 ≤200 行、未截断」，
而真实表可能远多。明细查询本就想封顶，影响有限；**不改码**，但报告/insight 文案对
明细查询不要强调「共 N 行」。

### v2 测试要求（全、广、通用）

- 断言一律针对**捕获到的真实 prompt**（`_generate_sql` / `_plan`），不再只钉常量；
- 外键链路：循环断言 4 张事实表的全部主外键映射都进了 `_generate_sql` prompt；
- 当前日期：断言 prompt 含 `date.today().isoformat()`（运行当天动态值，任何一天都成立）；
- JOIN 规则：LEFT JOIN / 禁 RIGHT JOIN / GROUP BY 完整性 / LIMIT 200 / 子查询分层 / 不臆造列；
- 补 `extract_sql` 鲁棒性：模型若仍输出 `-- 注释\nSELECT…`，能安全剥出纯 SELECT 不产生残缺 SQL；
- 时间 / 数组规则断言保留。
