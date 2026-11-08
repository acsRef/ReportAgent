> 状态: 已完成

# 「SQL执行失败: 」空报错的归因与修复：scope 值没过数据字典 + 失败文案会拼空串

## Context（背景）

2026-11-08 真机（会话 `4cadf946…`）：用户先问「帮我分析一下销量」，再补一句
**「生成2023年，华南区域的吧，然后标注下 最高和最低的，按照月份来生成」**，
确认后报告 v1 落成 **FAILED**，界面上只显示一行 `执行失败  SQL执行失败:`（冒号后面是空的）。

逐层取证：

| 层 | 事实 |
|---|---|
| 报告落库 | `report_payload.error = {code: EXECUTION_ERROR, kind: syntax, message: "SQL执行失败: "}`；`query_snapshot` 为 NULL |
| 需求卡 | `scope=["华南区域"]`、`time_range="2023年"`、`top_period={unit:month, metric:销量, direction:both}` |
| 选期解析 | 直接调 `resolve_top_period(unit=month, direction=both, time_range=2023年, scope=["华南区域"])` → `ok=false, reason="empty"`；换成 `scope=["华南"]` → `ok=true`，指出 **2023年11月 / 2023年4月** 并给出确定性 filters |
| 数据 | `dim_store.region` 的真实取值是 `华东/华中/华北/华南/西北/西南` —— **没有「华南区域」**；2023 年数据存在（43669 行） |
| 日志 | 三次 validate 尝试，第 1 次「top-period contract violated」（模型没写出选期结构），后两次失败；repair 预算耗尽 → `DiagnosePolicy: clarify`（reason=`syntax: budget exhausted after validation`） |

**因果链**：scope 值没对齐（`华南区域` ≠ `华南`）→ 选期查询 0 行 → `resolved=false` →
prompt 退回「让模型自己写两段式 CTE」→ 模型写不出契约结构 → validation 连续失败 →
预算耗尽 → 构建失败时 `raw`（执行结果）为空、`parsed_raw` 为空 → `message=f"SQL执行失败: {''}"` → 界面上一片空白。

## Design（设计）

### D1 scope 值按数据字典做**确定性**归一

- `schema_hints.yaml` 新增 `dimension_values.区域`（真实取值，数据不是代码）；
- `registry.canonical_dimension_value(term, value)`：先精确匹配 → 再逐个剥掉口语后缀
  （`区域/地区/大区/省/市/县/区`）后匹配 → **仍不命中就原样返回**（需求层不猜数据）；
- `registry.canonical_scopes(values)`：批量 + 去重 + 去空，`ALL` 这类占位值保持原样；
- 两个产卡路径（`_requirement_project` 投影 / `_requirement_parse` 回落）统一过一遍，
  保证 SSE 下发的卡与落库的卡一致。

### D2 失败文案由「校验结论 → 执行错误 → 原始返回 → 兜底」按序取，绝不允许空串

`sql_graph._failure_detail(kind, validation, parsed_raw, raw)`：
1. 校验没过（含选期/明细契约）→ `validation.error`（就是那句人话）；
2. 执行返回错误 JSON → `parsed_raw.error`；
3. 都没有 → 原始返回截 200 字；
4. 仍然空（预算耗尽空转的形态）→ **固定兜底**「SQL 未通过校验或执行失败（已按预算重试，仍未得到有效结果）」。
`clarify` 与 `fail` 两个分支共用它；顺带把 `validate_sql` 的日志从 `sql[:60]`（换行开头时什么都看不到）
改成压缩空白后的 160 字，便于以后排查。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/knowledge/schema_hints.yaml` | 新增 `dimension_values.区域` |
| `backend/app/knowledge/registry.py` | `dimension_values()` / `canonical_dimension_value()` / `canonical_scopes()` |
| `backend/app/agent/requirement_analysis_graph.py` | `_normalize_card_scope()`，在两个产卡路径各调一次 |
| `backend/app/agent/sql_graph.py` | `_failure_detail()` + clarify/fail 分支接线；validate 日志可读 |
| `backend/tests/contracts/test_failure_attribution_fixes.py`（新） | 10 例（scope 归一 5 + 失败文案 4 + 兜底 1） |

## Verification（验证）

```bash
pytest backend/tests/contracts/test_failure_attribution_fixes.py -q   # 10 passed
```

真机复现（同一条问法，两步：先建草稿再 PATCH 成完整卡 → confirm）：

- 修复前：`SQL执行失败: `（空文案）+ `query_snapshot=NULL`；
- 修复后：卡上 `scope=["华南"]`；确认第一次因**无关的 LLM 超时**失败（`LLM_TIMEOUT`），
  `/retry` 后成功：`WHERE dim_date.year = 2023 AND dim_date.month IN (4, 11) AND dim_store.region = '华南'`
  → **2 行**（4 月 1328、11 月 …），`execution_status=SUCCESS`。

## Explicitly NOT doing（不做事项）

- 不改「标注 vs 只看」的语义（用户说「标注下最高和最低的月」，当前实现会**只取那两期**；
  这是需求层解释问题，需单独决策，见下文残留）；
- 不把 scope 归一做成「查库枚举」（需求层不连业务库，枚举放数据文件）；
- 不动 `LLM_TIMEOUT` 的文案映射（它现在说「查询超时,请缩小时间范围」，与实际原因不符，
  属 Reliability 文案表问题，另开）。

## 残留（下次处理）

1. **「标注」被当成「只看那两期」**：本例用户要的是「按月全出 + 标出最高/最低」，
   当前 `top_period` 语义是**选期**（只取那两期）。需要给 `top_period` 增加
   `purpose: select|annotate`（annotate 时不加过滤、只在报告层标注极值）。
2. **LLM 超时文案错位**：`LLM_TIMEOUT` 落到「查询超时,请缩小时间范围或维度后重试」，
   与实际原因不符。
