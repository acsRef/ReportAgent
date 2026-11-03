# Prompt 契约（意图识别必须可验证）

> 状态: 生效（2026-11-03）
> 适用：`requirement_parse` / `sql_plan` / `sql_generate` 三份 prompt。
> 目标：意图识别不能靠模型自由发挥——**确定性优先，模型只在不确定处说话，输出必被校验**。

## 一、为什么需要这份契约

实测缺陷：用户问「2024年的销售情况」，系统自己判定成「趋势分析」，按年分组只拿到 1 行，
报告写「数据量不足」。同类问题还有：把 `analysis_methods` 当必填项诱导模型瞎填、
澄清选项是写死的死表、MCP 返回的字段被丢掉、`今年` 被解析成示例里的 2024。

根因不是模型笨，是 prompt 只写了「说教」，没有契约：
没有输入优先级、没有输出校验、没有自检、没有金标回归。

## 二、7 段结构（三份 prompt 共用）

| 段 | 落点（现有 6 段容器） | 要求 |
|---|---|---|
| `BOUNDARY` | `system_contract` | ≤3 句：我是谁 / 只做什么 / 绝不做什么 |
| `INPUTS` | `inputs_priority` | 输入清单 + **可信度排序**（见 §三） |
| `DECISION RULES` | `task_contract` | 可枚举的 if/then 决策表，不写散文 |
| `OUTPUT CONTRACT` | `output_schema` | 精确 JSON，字段级规则，与解析器逐字段对齐 |
| `SELF-CHECK` | `self_check` | 5–7 条 yes/no，回答前逐条过 |
| `FORBIDDEN` | `safety_policy` | 每条都是「不得…」 |
| `EXAMPLES` | `knowledge/*.yaml` | few-shot 放数据文件，**与测试集互斥** |

## 三、输入优先级（冲突仲裁）

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 用户**本轮**原话 | 最高 |
| 2 | 上一轮已确认的需求卡 | 本轮未改写则继承 |
| 3 | 记忆里的偏好 | 只影响展示（图表类型），不影响查询语义 |
| 4 | 模型推断 | 最低，且必须落成 assumption 让用户确认 |

## 四、意图识别的三层

```
第1层 代码（确定性）：候选意图 / 可用字段 / 指标候选 / 时间解析
第2层 模型：在候选里选一个，或标 unclear；写追问句（选项必须引用真实字段）
第3层 服务端校验：intent ∈ 枚举；metric ∈ registry；option.column ∈ schema
```

- 能判的**先判**：`_deterministic_intent()` 覆盖「各区域 / 各品类 / 按月 / 明细 / 异常 /
  华东和华南比一下 / 哪个品类卖得最好」等常见说法；判出来就不再问模型。
- 判不出来才让模型澄清；模型给的选项还要过 schema 白名单（`_build_clarifications`）。
- 用户没表达分析角度时，模型给的 `analysis_methods` 一律被 `_guard_analysis_methods` 清空。

## 五、requirement_parse 输出契约

```json
{
  "intent": {"kind": "overview|trend|compare_region|compare_category|detail|anomaly|unclear",
             "evidence": "原话依据"},
  "metric": {"name": "销售额", "column": "fact_orders.order_amount", "source": "registry|user_text"},
  "time": {"range": "2024年7月", "granularity": "month"},
  "dimensions": ["时间", "区域"],
  "filters": [{"field": "region", "operator": "=", "value": "华东", "column": "dim_store.region"}],
  "detail_level": "aggregate|detail",
  "clarifications": [{"key": "analysis_intent", "question": "你想看哪种视角？",
                      "options": [{"label": "按月趋势", "value": "intent:trend", "column": "dim_date.month"}]}],
  "confidence": 0.8
}
```

字段级规则：

| 条件 | 必须 |
|---|---|
| `intent.kind=unclear` | 至少 1 条 `analysis_intent` 澄清 |
| `intent.kind=trend` | `time.granularity` 非空，且 schema 存在时间列 |
| `intent.kind=detail` | `detail_level=detail`，且不需要 metric / granularity |
| 指标解析不到 registry | 出 `clarifications.metric`，不得臆造列 |
| 任意 `option.column` | 必须存在于本 session 的 schema |

## 六、职责边界（三份 prompt）

| prompt | 只做 | 不做 |
|---|---|---|
| `requirement_parse` | 意图 / 指标 / 时间 / 维度 / 澄清 | 不碰表结构细节、不生成 SQL |
| `sql_plan` | 用哪张表、怎么 JOIN、哪些过滤 | **不重新推断意图**（卡片说啥就是啥） |
| `sql_generate` | 把 plan 翻成 PostgreSQL 语法 | 不解释业务、不加聚合/明细规则 |

明细模式是特例：plan 阶段清空 dimensions/聚合，**generate 阶段必须再声明一次**
（否则模型会把 target_metric 重新 SUM 起来，实测「给我今年华东的明细」返回 1 行）。

## 七、测试原则（用户 2026-11-03 明确要求）

1. **用真实问法**：口语、省略、相对时间、错别字；不抄 prompt 的 few-shot 原句
   （`test_real_user_questions_hygiene.py` 钉住）。
2. **只问「能不能查出来」**：执行成功、结构对（列/粒度/过滤）、必要时先澄清；
   不核对卡片字段细节。
3. **数量克制**：14 条封顶，7 类 × 2 条（`evaluation/real_user_questions.yaml`）。
4. **主干优先**：迭代期只跑 3 条 smoke；阶段完成跑 14 条 + 多轮 e2e；不跑上千个单测。

```bash
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_real_user_questions.py -s   # 14 条主干
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -s    # 多轮追问
pytest backend/tests/contracts/test_real_user_questions_hygiene.py -q       # 反抄袭（离线）
```
