# Prompt 契约化 + 真实问法测试（意图识别不许猜）

> 状态: 已完成

## Context

用户连着给了三条要求：

1. 「生成 SQL 这里的意图识别非常重要，所以绝对不能出错」——按系统提示词的写法重构 prompt；
2. 「测试用例需要调整到具体的问法，不要用提示词里的东西来测试」——用真实问法，且不能拿 prompt 示例自证；
3. 「优先测试后面的用例，那一千多个先不测，抓住主干，链路要通」——测试策略改成主干优先。

当时的实测缺陷：

```
输入：2024年的销售情况
卡片：methods=['trend_analysis']（模型自己塞的）、granularity=None、missing=[]
SQL ：GROUP BY dim_date.year WHERE year=2024  →  1 行
洞察：「数据量不足，无法进行趋势分析」
```

顺带发现的三处：`_schema_text` 只给表名+描述（MCP 返回的字段被丢）、澄清选项是写死的 5 张表、
`今年` 被解析成示例里的 2024（需求 prompt 没告诉模型今天几号）。

## Design

### 1. 意图识别三层

```
第1层 代码（确定性）：_deterministic_intent 覆盖「各区域/各品类/按月/明细/异常/华东和华南比一下」
第2层 模型：判不出来才澄清；选项必须引用 schema 真实字段
第3层 服务端：intent 枚举 / metric 必须在 registry / option.column 必须在 schema
```

- `_guard_analysis_methods`：用户没表达分析角度时，模型给的 methods 一律清空；
- `_build_clarifications`：模型生成的澄清项，选项列不存在就丢；有效选项 <2 整条丢；
- `_default_intent_clarification`：模型没问时，按**可用字段**补一条（有时间列才给「按月趋势」）。

### 2. 三份 prompt 补「输入优先级 + 自检」

- `INPUTS`：用户原话 > 已确认卡片 > 记忆偏好 > 模型推断；
- `SELF-CHECK`：7 条 yes/no（是否替用户决定视角 / 字段是否真实存在 / 年份是否按今天换算 …）；
- `today` 注入 requirement prompt（修「今年」→2024）。

### 3. 真实问法测试矩阵（14 条）

`evaluation/real_user_questions.yaml` + `backend/tests/e2e/test_real_user_questions.py`：
7 类 × 2 条（口语省略 / 相对时间 / 对比 / 明细 / 异常 / 模糊 / 完整明确），
断言只问「能不能查出来」：执行成功、行数区间、关键列、明细不得 GROUP BY、模糊必须先澄清。

`test_real_user_questions_hygiene.py` 离线钉三条：数量 14 封顶、问法不重复、
**不得与 prompt few-shot 原句相同**、至少一半是口语表达。

## Files to change

| 文件 | 变更 |
|---|---|
| `backend/app/agent/prompts/requirement_prompts.py` | INPUTS 优先级 / 分析角度规则 / clarifications 输出契约 / SELF-CHECK / today |
| `backend/app/agent/prompts/sql_prompts.py` | plan 与 generate 各加 SELF-CHECK；plan 不许重新推断意图 |
| `backend/app/agent/requirement_parser.py` | schema 渲染字段、确定性 intent、澄清校验、today 注入、明细不要求指标 |
| `backend/app/models/requirement.py` | `RequirementFieldKey = str`（澄清 key 由模型生成） |
| `backend/app/services/requirement_service.py` | `intent:` / `metric:` / `time:` / `scope:` 前缀的确定性映射 |
| `backend/app/agent/sql_graph.py` | `detail_level` 透传；generate 阶段再声明明细模式 |
| `backend/app/tools/report_tools.py` | 年份/月份等时间列不当度量（修「合计=2,024」） |
| `evaluation/real_user_questions.yaml` | 新增：14 条真实问法 |
| `backend/tests/e2e/test_real_user_questions.py` | 新增：真链路 14 条 |
| `backend/tests/contracts/test_real_user_questions_hygiene.py` | 新增：反抄袭与数量约束 |
| `docs/architecture/prompt-contract.md` | 新增：7 段结构 / 输出契约 / 仲裁表 / 测试原则 |

## Verification

```bash
# 主干（迭代期 / 提交前）
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_real_user_questions.py -s   # 14 条
REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_multiturn_followup.py -s    # 多轮
pytest backend/tests/contracts/test_real_user_questions_hygiene.py -q       # 反抄袭
pytest backend/tests/graphs/test_requirement_parser.py -q                   # 改动模块单测
```

真链路结果（2026-11-03，14/14 passed，314s）：关键几条——

| 问法 | 结果 |
|---|---|
| `2024年各区域销售额排名` | 6 行，区域/销售金额 |
| `2024年华东每月销售额` | 12 行，月份/销售额 |
| `华东和华南比一下` | 2 行（自动默认「今年 + 销售额」） |
| `哪个品类卖得最好` | 6 行，品类 |
| `给我今年华东的明细` | 200 行原始列（订单ID/日期/金额/门店/客户/产品） |
| `2024年的销售情况` / `帮我看下经营情况` | 均回到澄清，未静默出趋势 |

## Explicitly NOT doing

- 不跑 1000+ 全量单测（按用户要求，只跑涉及模块的单测 + 主干用例）
- 不换模型 / 不调 temperature
- 不改 SSE / 前端契约
- 不追求用例数量（14 条封顶）

## 落地记录（2026-11-03）

按主干优先跑了多轮真链路，逐个修掉四类问题：

1. **意图漏判**：`华东和华南比一下` 只认「对比/比较」不认「比一下」→ 加「维度词 + 比较语义」判定；
2. **过度追问**：`2024年各区域销售额` 已说「各区域」仍被问视角 → 确定性优先，直接判 compare_region；
3. **相对时间错年**：`今年` 解析成 2024（示例影响）→ requirement prompt 注入 today；
4. **明细退化**：`给我今年华东的明细` 返回 1 行聚合 → 明细指令在 generate 阶段再声明一次。

另修：`group_compare` 把「年份」当度量求和（每行「合计=2,024.00」）。

回归：主干 14 条 **14 passed / 314s**；多轮 e2e 待复跑；涉及模块单测 42 passed。
