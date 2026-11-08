> 状态: 已完成

# 「标注」不该被当成「只看」+ 模型超时不该说成查询超时

## Context（背景）

两个真机暴露、用户已经踩到的问题（2026-11-08 收口清单 A1/A2）：

### A1 「最高的那两个月，标注一下」被理解成「只看那两个月」

用户原话带「标注」，意思是**保留全区间，再把那两期标出来**（否则「标注」这个词没有意义——
只取两期根本不需要标注）。但 `RequirementTopPeriod` 只有一个语义方向：

- `app/agent/confirmed_execution_graph.py:_top_period_hint` 在 `resolved=True` 时直接给
  「把 `WHERE dim_date.month = 11` 写进查询」；
- `app/agent/sql_graph.py:_top_period_contract_violation` 更狠：SQL 里没带该期过滤就判
  validation 失败 → 进 repair 环 → 预算耗尽 → 用户看到失败。

于是「标注」必然收敛成「只看」。这是**契约缺一个字段**，不是模型不听话：
卡上没有地方存「是取那几期，还是全量并标出那几期」，下游只能按「取那几期」实现。

### A2 LLM 超时的用户文案说成「查询超时，请缩小时间范围」

`app/reliability/errors.py` 的 `_USER_MESSAGES` 只按 `kind` 查表，而 `classify_llm_exception`
把 LLM 超时也归成 `kind="timeout"` → 用户拿到的建议是「缩小时间范围或维度」。
真因是模型响应超时，这条建议**白试**（缩小时间范围不会让模型更快）。
`ErrorCode.LLM_TIMEOUT` 其实早就在 envelope 里，只是文案层没用它。

## Design（设计）

### D1 `top_period.purpose`：select / annotate

新增字段（默认 `select`，旧卡与旧会话零影响）：

```python
class RequirementTopPeriod(BaseModel):
    direction: Literal["max", "min", "both"]
    unit: RequirementGranularity
    metric: str | None = None
    # select   = 只取极值那几期（现有行为）
    # annotate = 保留全区间，另加一列标出哪几期是极值期
    purpose: Literal["select", "annotate"] = "select"
```

**判定是确定性的，不看模型**：`requirement_parser` 新增
`_TOP_PERIOD_ANNOTATE_WORDS = ("标注", "标记", "标明", "注明", "标一下", "标出来", "打标")`，
原话命中即 `annotate`。产卡后再由 `requirement_analysis_graph._normalize_card_top_period`
统一用原话覆盖一次——和 `_normalize_card_scope` 同一处、同一手法（模型给什么都不算数）。

**annotate 的 SQL 形态**（两个注入点同步改）：

```text
【标注模式（确定性）】保留全区间数据，不要只取那一期；
必须新增一列标记：
  CASE WHEN <期列> IN (4, 11) THEN '是' ELSE '否' END AS "是否最高期"
列名固定用「是否{最高|最低}期」，前端与契约都按这个名字认。
```

**契约校验也分叉**（`_top_period_contract_violation`）：

| purpose | 校验什么 | 违规文案 |
|---|---|---|
| `select` | SQL 必须带该期过滤（现有行为） | 缺少「只取选定那一期」的过滤条件 |
| `annotate` | SQL 必须同时出现：①期列 ②期值 ③「是否…期」标记列；**且不许**把它写成只有该期的 WHERE | 缺少「标注哪几期是最高期」的标记列 |

**前端**：`topPeriodText` 在 `annotate` 时输出「销售额最高的月份（标注）」，
确认条让人一眼看出「是全量 + 标注」而不是「只看那一期」。

### D2 文案按 code 分叉

`user_message(kind)` → `user_message(kind, code=None)`，新增 `_CODE_MESSAGES` 覆盖表：

| code | 文案 |
|---|---|
| `LLM_TIMEOUT` | 模型响应超时，请稍后重试；把问题拆小一点也有帮助 |
| `MCP_TIMEOUT` | 数据字典检索超时，请稍后重试 |

只有 `code` 命中覆盖表才改变文案，`SQL_TIMEOUT` / 未知 code / 不传 code 一律保持原样
（`QUERY_*` 与既有文案是稳定契约，`test_reliability_errors.py` 里逐字钉着）。
调用点两处：`main.py` 两个泛化异常出口从 `envelope.kind` 改成 `(envelope.kind, envelope.code)`。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/models/requirement.py` | `RequirementTopPeriod` 加 `purpose` |
| `backend/app/agent/requirement_parser.py` | 标注词表 + `_top_period_from_query` 返回 `purpose` |
| `backend/app/agent/requirement_analysis_graph.py` | 新增 `_normalize_card_top_period`，两个产卡节点都调 |
| `backend/app/agent/confirmed_execution_graph.py` | `top_period_state` 带 `purpose`；`_top_period_hint` annotate 分支 |
| `backend/app/agent/sql_graph.py` | 生成 prompt annotate 分支 + `_top_period_contract_violation` 分叉 |
| `backend/app/reliability/errors.py` | `_CODE_MESSAGES` + `user_message(kind, code)` |
| `backend/app/main.py` | 两处泛化异常出口传 `envelope.code` |
| `frontend/src/types/requirement.ts` | `purpose?: 'select' \| 'annotate'` |
| `frontend/src/components/workbench/slotQuestion.ts` | `topPeriodText` annotate 文案 |
| 测试（各自独立） | `test_top_period_annotate.py` / `test_error_user_messages.py`；前端 `slotQuestion.test.ts` 补两例 |

## Reused existing utilities（复用工具）

- `_normalize_card_scope` 的「产卡后确定性归一」模式（同一节点、同一位置）；
- `knowledge.time_bucket_column` / `_GRANULARITY_COLUMNS`（期列已有单一来源）；
- `metric_preview_service.resolve_top_period`（选期数值仍由它算，不改）；
- `_USER_MESSAGES` / `normalize_kind` / `user_recoverable`（不新造错误通道，只加一层覆盖）。

## Verification（验证）

聚焦用例（不跑整套）：

```bash
pytest backend/tests/contracts/test_top_period_annotate.py \
       backend/tests/contracts/test_error_user_messages.py -q
npx vitest --run src/components/workbench/__tests__/slotQuestion.test.ts
```

真机（两轮，各自断言）：

```bash
python .e2elogs/probe_top_period_annotate.py   # 「2023年最高的那两个月的明细，标注一下」
                                               # → 卡上 purpose=annotate；SQL 无 month IN 过滤、
                                               #   有「是否最高期」列；行数 = 全区间明细量级
```

## Explicitly NOT doing（不做事项）

- 不做「任意列/任意条件的标注」（只标极值期，其他标注需求另开 plan）；
- 不改 `select` 的既有行为（默认值不变、契约不变、旧卡不迁移）；
- 不新增 SSE 事件或错误码（只改文案查表与卡字段）；
- 不让模型判断 purpose（词表确定性判定，模型猜的不算数）。

## 落地记录（2026-11-08）

两件事都落了，但**真机跑出来的问题比 plan 写的多三个**，逐个记下来（都在原 plan 之外，
但同属「标注必须真的看得见」这条链，没越界去改别的）。

### A1 标注语义 ✅

- `RequirementTopPeriod.purpose: select | annotate`（默认 select，旧卡零迁移）；
- 词表 `_TOP_PERIOD_ANNOTATE_WORDS`（标注/标记/标明/注明/标一下/标出来/打标/标出）→
  `_top_period_purpose()`；`_top_period_from_query` 返回体带 `purpose`；
- `requirement_analysis_graph._normalize_card_top_period`：产卡后用原话归一，
  **模型没给 top_period 时按原话补建**（同一句两次跑一次给一次不给，实测）；
- `_top_period_hint` / `sql_graph` 生成 prompt 的 annotate 分支：全区间 + `CASE WHEN` 标记列；
- `_top_period_contract_violation` 分叉出 `_top_period_annotate_violation`：
  必须有「是否…期」列，不许退化成只看；`CASE WHEN` 但别名不约定也判违规；
- 前端 `topPeriodText` annotate → 「销售额最高的月（全量标注）」。

### A2 文案分叉 ✅

`user_message(kind, code=None)` + `_CODE_MESSAGES{LLM_TIMEOUT, MCP_TIMEOUT}`；
`main.py` 两个泛化异常出口传 `envelope.code`；其余 kind 文案逐字不变。

### 真机追加修的三个坑（都不在 plan 里，但不修功能就是假的）

1. **期值算错年**：卡上 `time_range` 被规范化成纯年份「2023」，而
   `metric_preview_service._YEAR` 要求带「年」字 → 解析失败 → 选期按**全库**算 →
   用户问 2023，SQL 标出的是 `year = 2024 AND month = 6`。
   修：加 `_YEAR_BARE`（整串只有一个四位年份时也认；`2023-11` 这类仍不认）。
2. **标注列被当常量删掉**：明细按时间倒序只取最近 200 条，若全在同一月，
   「是否最高期」整列恒定 → `_drop_constant_columns` 删除 → 界面上**根本看不到标注**。
   修：`_drop_constant_columns(keep=...)`，标注列豁免；`_annotation_columns` 按
   **state + 列名形状**双来源识别（见坑 3）。
3. **LangGraph 丢掉未声明的 state 键**：`top_period` 没写进 `ConfirmedExecutionState`
   → 报告节点永远读到 None → 豁免没生效。修：补 `top_period: Optional[dict]` 字段，
   同时保留「是否…期」列名形状兜底（下次谁再漏声明也不会静默失效）。
4. **「按标记列排序」是错的方向**：本想保证「是」排前面，实测前 200 行**全是「是」**
   → 标注列又变常量、又被删。改为「仍按时间倒序，不要按标记列排序」。
   （代价：若那几期不在这 200 行里，标记列会全是「否」——这是 LIMIT 的客观结果，
   语义上仍然正确，见「不做事项」。）

### 验证结果

- 后端聚焦 **122 passed**（`test_top_period_annotate.py` 28 + `test_error_user_messages.py` 17
  + `test_reliability_errors.py` / `test_fault_inject.py` / `test_top_period_detail_intent.py` /
  `test_turn_understanding.py` / `test_requirement_card_mirror.py`）；前端 `slotQuestion` 24 passed、
  `tsc -b` 干净；
- 真机 `python .e2elogs/probe_top_period_annotate.py` **ALL PASS**：
  卡上 `purpose=annotate`；SQL 是 `... CASE WHEN dim_date.year = 2023 AND dim_date.month = 11
  THEN '是' ELSE '否' END AS "是否最高期" ... WHERE dim_date.year = 2023 ORDER BY
  full_date DESC LIMIT 200`；表格 5 列（含「是否最高期」）、200 行；
- 顺手修了一个 **pre-existing red**：`test_build_top_period_sql_fail_open_on_unknown_range`
  钉的是「未知时间范围返回 None」，而 `e8493f8`（2026-11-05）已改成「覆盖全部数据」，
  测试没跟着改，一直红着。改成断言新行为。
- 探针会话（`annotate-*` 8 个）已按前缀清理，库中会话从 21 回到 13。

