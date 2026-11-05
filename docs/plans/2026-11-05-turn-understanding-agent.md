> 状态: 进行中

# Turn Understanding Agent：多轮任务理解 + 确定性状态归并 + 可报告性裁决

## Context（背景）

用户拍板（2026-11-05，长评，摘要）：

> 「不要简单地新增一个 IntentNode，而是做一个更完整的 **Turn Understanding Agent**，
> 再配合一个独立的确定性校验节点，决定是否具备生成 Report 的条件。」
> 「不要把这个节点做成更聪明的意图分类器，而要把它做成真正理解业务任务、管理多轮任务变化的 Agent。」
> 「不要让理解 Agent 和 requirement_parse 简单串联成两个 LLM 调用——两个模型可能理解出不同的需求。
> 更合理的是重构 requirement_parse：由强模型一次生成完整 TurnPlan，再由确定性 Reducer 和 Validator 处理。」

### 现状的架构空缺（真机证据）

第一轮 `2024年华东销售趋势和异常`，第二轮 `销售额最多和最小的具体情况是什么`：

| 需求卡实际值 | 应有值 | 为什么会错 |
|---|---|---|
| `granularity=week` | `month`（选期单位） | 从上一轮**无条件继承**粒度 |
| `detail_level=detail` | `null` | 模型把「具体情况」猜成「明细」 |
| `analysis_methods=["anomaly_detection"]` | `[]` | 从上一轮继承 |
| `top_period=null` | `{both, month}` | 选期识别只认单方向 + 紧跟单位 |

根因不是「prompt 写得不好」，而是：**没有任何节点负责理解「这一轮和上一轮任务的关系」**。
`_classify_intent` 只分业务类型，`_requirement_parse` 只从当前这句话抽槽位。
「继承哪些、改哪些、清哪些」目前由一堆散落的关键词规则各自决定，互相冲突。

## Design（设计）

### D1 新增 `TurnPlan`（强模型一次产出，不再是「槽位 + 猜」）

```python
class AnalysisGoal(BaseModel):
    type: Literal["overview","trend","compare","ranking","anomaly",
                  "detail_rows","extreme_period","distribution"]
    dimension: str | None = None        # 比较/分布的目标维度
    direction: Literal["max","min"] | None = None
    unit: RequirementGranularity | None = None   # extreme_period 用
    metric: str | None = None

class TurnPlan(BaseModel):
    conversation_act: Literal["new_task","follow_up","modify_task",
                             "answer_clarification","chitchat"]
    analysis_goals: list[AnalysisGoal] = []
    preserve_fields: list[str] = []     # 从上一轮任务继承
    update_fields: dict[str, Any] = {}  # 本轮改写（只允许白名单字段）
    clear_fields: list[str] = []        # 本轮作废
    uncertainties: list[Uncertainty] = []
    evidence: list[str] = []            # 每条判断对应的用户原话证据
```

### D2 `ActiveAnalysisTask`：唯一任务状态（RequirementCard 变成它的投影）

```python
class ActiveAnalysisTask(BaseModel):
    revision: int = 0
    time_range: str | None = None
    scope: list[str] = []
    metrics: list[str] = []
    granularity: RequirementGranularity | None = None
    goals: list[AnalysisGoal] = []
    detail_level: RequirementDetailLevel | None = None
```

`RequirementCard` **契约不变**，由 `project_card(task)` 确定性投影得到：
`goals` 里 `trend/compare/anomaly` → `analysis_methods`，`extreme_period` → `top_period`，
`detail_rows` → `detail_level`。这样 SQL / Report / 前端**零改动**。

### D3 确定性 Reducer（模型不许直接改历史状态）

```python
def reduce_task(prior, plan) -> tuple[ActiveAnalysisTask, TaskRevision]
```

规则（纯函数，可单测）：
- `conversation_act=new_task` → 丢弃 prior，从 plan 重建；
- `follow_up/modify_task/answer_clarification` → 以 prior 为基：
  1. `clear_fields` 先清；2. `update_fields` 覆盖；3. `preserve_fields` 保留；
  4. **未在三个集合里出现的字段：沿用 prior**（缺省=继承，和现有 supplement 语义一致）；
- 每轮产出一条 `TaskRevision`（轮次 / 变更项 / 变更前后），供审计与前端展示。

### D4 Reportability Validator（确定性裁决，模型不参与）

```
READY                 —— 有 metric + 至少一个 goal 且 goal 可执行
NEEDS_CLARIFICATION   —— 缺 metric / 缺时间 / extreme_period 缺 unit 等（列 blockers）
UNSUPPORTED           —— goal 或 metric 在注册表/Schema 里不存在
```

输出 `{verdict, blockers[], notes[]}`；`NEEDS_CLARIFICATION` 的 blockers 会被翻译成
现有的 `RequirementMissingField`（复用澄清条与「一次一问」）。

### D5 模型分工（本阶段只接 Turn Understanding）

| 节点 | 模型 |
|---|---|
| Intent Router | 规则 + 现有主模型（不改） |
| **Turn Understanding** | `LLM_TURN_MODEL`（未配置则回落到 `LLM_MODEL`） |
| SQL Plan / Generate / Report | 现有主模型（不改） |

`reasoning_content`（若 provider 返回）只进 trace/日志，**不直接展示给用户**。

### D6 落地方式（风险控制）

图里新增三个节点，`TURN_UNDERSTANDING=1` 时启用：

```
security_guard → classify_intent → data_agent
   → turn_understand（强模型，产 TurnPlan）
   → task_reduce（确定性，产 ActiveAnalysisTask + Revision）
   → reportability（确定性，产 blockers）
   → requirement_preview → persist_draft
```

`=0`（默认）时仍走现有 `requirement_parse`。这样 A/B 用同一个开关切换，
出问题能立刻回退——正是用户强调的「不要一次性把所有节点都换掉」。

## Files to change（文件改动）

| 文件 | 改动 |
|---|---|
| `backend/app/models/turn_plan.py` | 新增 TurnPlan / AnalysisGoal / ActiveAnalysisTask / TaskRevision / ReportabilityVerdict |
| `backend/app/agent/turn_understanding.py` | 新增：prompt 构建、`understand_turn()`、`reduce_task()`、`assess_reportability()`、`project_card()` |
| `backend/app/agent/prompts/turn_plan_prompts.py` | 新增：Turn Understanding 的 system prompt（含任务状态、Schema 摘要、输出 JSON 契约、SELF-CHECK） |
| `backend/app/agent/requirement_analysis_graph.py` | 新增 3 个节点 + 条件边（flag 控制） |
| `backend/app/llm/config.py` | `turn_model` / `turn_enable_thinking` / `turn_thinking_budget` |
| `backend/app/infra/trace/`（可选） | 记录 `reasoning_content` 摘要 |
| `backend/tests/graphs/test_turn_understanding.py` | 新增：reducer 三态 / clear / update / 未知字段忽略、reportability 三档、project_card 投影、prompt 含任务状态 |
| `evaluation/turn_understanding.yaml` | 新增：8 条真实多轮用例（用户给的清单）+ 期望 conversation_act / goals / preserve / clear |

## Reused existing utilities（复用工具）

- `RequirementCard` 契约、`slot_states/slot_order`、澄清与确认条渲染：**全部不改**；
- `parse_requirement` 的确定性内核（`_top_period_from_query` / `_granularity_from_query` /
  `_deterministic_intent` / 口径槽位）继续作为 **Reduction 的规则源**，不重写；
- `ContextRuntime.build()` 提供对话/记忆上下文；`traced_node` + `ProgressTraceHandler` 出 trace；
- LLM Adapter（`app/llm`）承载模型切换，不在 Agent 里拼 provider 参数。

## Verification（验证）

1. **离线（无 LLM）**：`pytest backend/tests/graphs/test_turn_understanding.py`
   - reducer：new_task 丢弃 prior / follow_up 继承未提及字段 / clear 清掉 / update 覆盖；
   - reportability：完整→READY；缺 metric→NEEDS_CLARIFICATION；未注册指标→UNSUPPORTED；
   - project_card：`extreme_period×2` → `top_period.direction="both"`；
     两轮真实对话的卡片与人工期望逐字段相等。
2. **真链路**：`TURN_UNDERSTANDING=1` 下重跑用户那两轮，断言卡片
   `gran=month / detail=None / methods=[] / top_period(both)`，SQL `month IN (11,2)`，2 行。
3. 模型对比（Phase 2，独立一步）：三个候选模型（`Qwen/Qwen3.8-27B`、
   `deepseek-ai/DeepSeek-V4-Flash`、`Qwen/Qwen3.5-35B-A3B`）跑同一份
   `evaluation/turn_understanding.yaml`，记录：意图准确率 / 继承修改准确率 /
   澄清判断 / JSON 合法率 / 延迟与成本。

## Explicitly NOT doing（不做事项）

- **不同时换 SQL / Report 模型**（否则无法归因）；
- 不让模型直接改 `ActiveAnalysisTask`（只能产出计划，Reducer 裁决）；
- 不让模型决定「跳过用户确认」（执行授权仍在后端）；
- 不在本阶段做前端任务变更日志 UI（先落数据与 trace，UI 后置）；
- 不删除旧的 `requirement_parse` 路径（flag 关时仍是唯一路径，回退即用）。

## 落地记录（Phase 1，2026-11-05）

### 已实现

| 组件 | 文件 | 说明 |
|---|---|---|
| `TurnPlan` / `AnalysisGoal` / `Uncertainty` | `backend/app/models/turn_plan.py` | 强模型一次产出的「这一轮想干什么」；**没有**自评置信度，只有原话证据 |
| `ActiveAnalysisTask` / `TaskRevision` | 同上 | 会话级任务状态 + 每轮变更审计 |
| Turn Understanding prompt | `backend/app/agent/prompts/turn_plan_prompts.py` | 含任务状态/对话/schema 摘要 + 硬规则（「具体情况」≠ 明细、别自动继承上一轮方法、极值缺单位默认按月） |
| `understand_turn` / `reduce_task` / `assess_reportability` / `project_card` | `backend/app/agent/turn_understanding.py` | 模型理解 → 确定性归并 → 确定性裁决 → 投影成既有 `RequirementCard` |
| 任务审计持久化 | `backend/app/infra/db/analysis_task_repository.py` | `agent.analysis_task_revision`（append-only，按需建表） |
| 图节点 | `requirement_analysis_graph.py` | `turn_understand → task_reduce → reportability → requirement_project`，`TURN_UNDERSTANDING=1` 启用；失败自动回落旧 `requirement_parse` |

### 关键设计落点

- **一次模型调用**：不再串两个模型，避免「两个理解互相覆盖」；
- **模型不碰状态**：只产出 TurnPlan，归并由 `reduce_task`（白名单字段 + 类型收敛 + 非法值忽略）；
- **代码有否决权**：模型把「具体情况」读成 `detail_rows` 时，原话没有明细词就丢掉该目标
  （`_veto_goals`）——这是「模型理解、代码裁决」的实证；
- **可报告性独立**：`READY / NEEDS_CLARIFICATION / UNSUPPORTED` 由注册表与目标结构确定，
  `NEEDS_CLARIFICATION` 的 blockers 直接投影成既有澄清项（复用「一次一问」UI）。

### 验证

- 离线单测 `backend/tests/graphs/test_turn_understanding.py` **12 passed**：
  继承 / 改写 / 清空 / 新任务 / 未知字段忽略 / 双向目标 / 三档可报告性 / 投影 / 两轮真实对话复现。
- 真链路（`TURN_UNDERSTANDING=1`，两轮）：

```
turn1  2024年华东销售趋势和异常  → gran=week  metrics=[销售额] scope=[华东] time=2024年
turn2  销售额最多和最小的具体情况是什么
       gran=month  detail=None  methods=[]  scope=[华东](继承)  time=2024年(继承)
       top_period={both, month, 销售额}
SQL    month IN (11,2) → 2 行：2月 5,444,341.66 / 11月 9,437,879.30
```

### 未完成（Phase 2）

- **模型对比**：`LLM_TURN_MODEL` 已支持，但尚未跑 Qwen3.8-27B / DeepSeek-V4-Flash /
  Qwen3.5-35B-A3B 的三方对比，也还没写 `evaluation/turn_understanding.yaml` 的 8 条用例；
  当前真链路用的是主模型（V3）产 TurnPlan。
- 前端「任务变更日志」UI 未做（数据已落 `agent.analysis_task_revision`）。
- 默认开关仍为关（`TURN_UNDERSTANDING` 未设置时走旧路径），等模型对比后再切默认。
