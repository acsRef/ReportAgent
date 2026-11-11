# SQL 过滤写进 LEFT JOIN 的 ON：范围/时间过滤静默失效 + NULL 分组冒充「最高的那个月」

> 状态: 已完成（2026-11-11）

## Context

真机会话 `c3f0cf93-e583-4944-a71f-c19cfa0fcf96`（用户第一轮「分析下去年华南区域的销售」，第二轮「每个月的情况也都说一下吧，用柱状图」）报告已出，但**结论是错的**，而且错得不显眼——用户唯一能察觉的线索是核心发现里出现了「**None月 最高**」。

三个数逐位核对过库（不是估算）：

| 报告里的数 | 实际含义 | 库内真值 |
|---|---|---|
| 结论合计 `937,958,021.14` | 全库所有订单（全国 × 4 年） | `ALL_years_nationwide` = 937,958,021.11 |
| 月度 10 行 `206,836,756` | 2026 年 1–10 月**全国** | `2026_jan_oct_nationwide` = 206,836,755.71 |
| `None月` `731,121,265.40` | 非 2026 年**全国** | `not_2026_nationwide` = 731,121,265.40 |

用户问的是「华南每月」，真值是 **39,358,746.46**（1–10 月），合计高估 **23.8 倍**。

两个缺陷叠在一起：

1. **`LEFT JOIN ... ON` 里放取值过滤**，等于没过滤。`LEFT JOIN` 匹配不上仍保留左表整行，于是非华南订单（`dim_store.*` 为 NULL）、非 2026 年订单（`dim_date.*` 为 NULL）全部留下来；`GROUP BY dim_date.month` 把后者归成一行 `month = NULL`。
   ```sql
   LEFT JOIN dim_store ON fact_orders.store_id = dim_store.store_id AND dim_store.region = '华南'
   LEFT JOIN dim_date  ON dim_date.full_date = fact_orders.order_date AND dim_date.year = 2026
   ```
2. **报告层把 `NULL` 拼成「None月」还当冠军**：`report_graph._fallback_headline` 用 `str(r.get(label_key, ""))`，`None` → 字符串 `"None"`，再经 `_format_period_label` 拼成 `f"{value}月"`。

第一轮同样中招，只是没有 None 可看：`SELECT SUM(order_amount) ... WHERE region='华南'` **没有任何年份谓词** → 返回 1.84 亿（华南 2023–2026 四年合计），「去年」的真值是 47,841,705.30。根因是「去年」是**相对时间**，`time_range_from_query` 只认绝对年份（刻意如此），模型那一轮没给 `time_range`，于是既没有默认、也没有换算。

评测为什么没拦住：`execution_accuracy = 1.0` 是 20 条基线的结论，其中没有「区域过滤 + 时间趋势 + 外连接」这个形状。现有 6 道 SQL 闸只判「能不能安全执行」，不判「语义对不对」。

## Design

原则不变：**Agentic where uncertainty exists, deterministic where correctness matters.** 这里没有不确定——「plan 要求了范围过滤」和「SQL 把过滤写进了 ON」两件事都能确定性判定，所以给确定性闸，而不是再改 prompt。

### C1 必需过滤缺失（`_missing_filter_violation`）

从 plan 取**必需过滤值**：

- `plan.time_range` 里的 4 位年份（`2025年` / `2026年1月-3月` → `2025` / `2026`）；没有年份（`今年`、`最近30天`）则不判——那些依赖 today，交给 prompt。
- `plan.filters` 里的取值的**字面量部分**（`区域=华南` → `华南`）；`filters` 是自由文本，只认出现在 SQL 里的中文/字母取值，纯数字不判（避免把阈值当过滤）。

SQL 文本里一个都没有 → 违规，交 repair。

### C2 必需过滤落在外连接 ON（`_join_filter_contract_violation`）

sqlglot 解析出所有 `exp.Join`，只看 `LEFT` / `RIGHT` / `FULL`（`INNER JOIN` 的 ON 与 WHERE 等价，不判）：

- 取 `join.args["on"]` 的 SQL 文本；
- 若其中出现**C1 那批必需过滤值**（`'华南'` / `2026`）→ 违规，要求把取值过滤移到 `WHERE`，ON 里只留连接键。

为什么只认「必需过滤值」而不做「外连接里不许有常量」的全局禁令：`LEFT JOIN dim_promotion p ON p.promo_id = o.promo_id AND p.active = true` 是合法外连接条件（「匹配活跃促销，否则留空」），全局禁令会打死它。以 plan 要求为锚，误报面收敛到「用户明确要求的那个过滤被放错层」。

### C3 NULL 分组不再冒充冠军（`_fallback_headline`）

- `NULL` / 空串标签 → 显示「(未分组)」；
- 「最高」只在**有标签的分组**里选；
- 另起一句说明有 N 行未归组（把数据问题摆到用户面前，而不是藏进一个漂亮的百分比）。

### C4 NULL 分组进可观测（`_evaluate` 告警，不做硬拦截）

SUCCESS 且结果里时间类列的值为 `NULL` → `logger.warning("SUSPECT_NULL_GROUP ...")`，带列名与行数。

**为什么不做拦截**：维表真缺行时 NULL 分组是数据事实，硬拦会把本来成功的查询变成 FAILED（`MAX_SQL_REPAIR_RETRIES=2` 之后必挂）。根因由 C1/C2 在生成侧拦住，C4 只负责「下次再出现，日志里看得见」。

### C5 「去年/前年」确定性换算（`time_range_from_query` 增 `today`）

`time_range_from_query(query, *, today=None)` 新增相对年份：`去年` → 前一年、`前年` / `上一年` → 前两年，输出 `2025年` 形态。

只**新增**能力：此前这类问法返回 `None`（模型漏抽就永久没有时间范围），现在补一个确定性值；`resolve_time_range` 的「已有值优先」不变，模型给了值仍然尊重。`今年` 不换算（现有链路靠 prompt 注入 today + `default_year`，两套规则会打架）。

## Files to change

| 文件 | 改动 |
|---|---|
| `backend/app/agent/sql_graph.py` | 新增 `_required_filter_values` / `_missing_filter_violation` / `_join_filter_contract_violation`；`_validate` 里按既有契约族接线；`_evaluate` 加 NULL 分组告警 |
| `backend/app/agent/report_graph.py` | `_fallback_headline`：NULL/空标签 → 「(未分组)」，不参与最高评选，加未归组说明 |
| `backend/app/agent/requirement_parser.py` | `time_range_from_query(..., today=None)` + 相对年份换算；`resolve_time_range` 透传 `today` |
| `backend/tests/graphs/test_join_filter_contract.py` | 新增：C1/C2 正反例（含误报防线） |
| `backend/tests/graphs/test_report_number_grounding.py` | 加 NULL 分组用例 |
| `backend/tests/contracts/test_requirement_projection_backfill.py` | 加「去年/前年」用例（固定 `today`） |
| `evaluation/baseline_cases.json` | 新增 `followup-monthly-after-region-scope` golden |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `validate_sql` / `check_sql_safety`：安全链**一行不改**，本闸只在「安全 + EXPLAIN 已过」之后追加语义契约。
- `_detail_contract_violation` / `_top_period_contract_violation` / `_comparison_contract_violation` 的既有模式：纯函数返回中文整改指令 → `validation = {"valid": False, "error": ..., "error_kind": "syntax"}` → `DiagnosePolicy` → `repair_sql`（预算 2）。
- `RepairContext` / `_format_repair_ctx`：违规文案原样进 repair prompt 的「错误」段。
- `sqlglot`（`sql_tools.py` / `sql_graph.py` 已在用，postgres dialect）。
- `_fallback_headline` 内部既有的 `measure_keys` / `is_time_like` / `_format_period_label`。
- `resolve_time_range`（两需求流水线的唯一时间决策点）。
- `evaluation.runner` 的 golden 执行与 `compare_result_sets`（行多重集比对，不需要新比较器）。

## Verification

1. **定向 pytest**（不跑全量，按用户要求）：
   ```
   pytest tests/graphs/test_join_filter_contract.py tests/graphs/test_report_number_grounding.py \
          tests/contracts/test_requirement_projection_backfill.py tests/graphs/test_detail_contract.py
   ```
2. **真链路复测**：重跑同一会话两轮，断言第二轮 `合计 == 39,358,746.46`、结果无 NULL 分组、图表是 10 组而不是 11 组。
3. **评测单例**：`python -m evaluation.runner --only-id region-filter-with-trend`。

### 实测结果（2026-11-11）

| 检查 | 结果 |
|---|---|
| 定向 + graphs/contracts 全跑 | 1363 passed（23s） |
| 全量 backend | **1820 passed / 18 skipped**（133s，基线 1783 → +37） |
| 真链路第一轮「分析下去年华南区域的销售」 | 卡片 `time=2025年`（此前空 → SQL 无任何年份谓词）；SQL = `WHERE dim_date.year = 2025 AND dim_store.region = '华南'`；**47,841,705.30**（此前 1.84 亿） |
| 真链路第二轮「每个月的情况也都说一下吧，用柱状图」 | 继承 `2025年 + 华南`（此前漂成「今年」）；12 行、`sum = 47,841,705.30`、**0 个 NULL 分组**、图表 bar；逐月数值与库内真值逐位一致 |
| 报告核心发现 | 不再出现 `None月`；依次报「1月 / 2月 / 6月 / 8月」等真实月份 |
| 新 golden `followup-monthly-after-region-scope` | 1/1 pass，`execution_accuracy 1.0`，T2（54.1s） |

一处必须说清：**这次真链路复测里模型自己写对了**（过滤写在 WHERE），C1/C2 没有被触发——
事故那次的 SQL 是采样结果，同一句问法不保证每次都错。所以闸门的价值在**确定性兜底**，
其有效性由 19 条单测 + 3 条 `_validate` 接线测试证明，而不是靠这次跑通。
本轮真正把数字改对的是 C5（「去年」→ `2025年` 的确定性换算）。


## Explicitly NOT doing

- **不做静默重写**：检测到「过滤在 ON 里」时不自动改写成 WHERE——改语义不可逆，走 repair 让模型带着明确指令重写。
- **不做「外连接不许有常量」的全局禁令**：会误杀合法的 `p.active = true` 外连接条件。
- **不新增错误码**：复用 `error_kind="syntax"` → `repair_sql`，不动 `DiagnosePolicy` 决策表与 `reliability/errors` 码表。
- **不把 NULL 分组做成硬失败**（理由见 C4）。
- **不碰**记忆 / 检索 / 图表偏好 / MCP 链路。
- **不修**已知缺口：`/confirm` 的 `user_query` 为空、误路由率未埋点、T3 只有 1 例。
