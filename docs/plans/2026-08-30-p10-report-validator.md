# P10 实施：Report Runtime——ReportSpec schema 固化 + 三层 Validator + versioning 语义收编

> 状态: 已完成（2026-08-30，p9-reliability 分支续作；T1–T6 全绿，用户拍板 FAILED 语义 + KPI 只落机制）
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §二·二目标目录（report/ 三件套）+ §十二 Report Runtime 与 Fact Checker 分层 + §392 P10 验收清单 + §451/§465 迁移映射 + [docs/architecture/report-runtime.md](../architecture/report-runtime.md)（P1 冻结契约）+ CLAUDE.md 宪法 §10 + [[memory:p9-landed]]（REPORT_VALIDATION_ERROR 码已就位）
> 协作: P9 已落 p9-reliability 分支（891 passed）；本 plan 仅 P10

## 落地记录（2026-08-30）

- commit 链：plan `8249322` → T1 spec `a3afb0e` → T2 validator `6b9530a` → T3 versioning `e5839fb` → T4/T5 接线 `163efc2` → 收尾（含 ErrorCode 引用修正）
- **落地偏差 1（T3）**：`resolve_verdict` 砍掉——无真实消费点，不写无消费代码；versioning.py 只落 `resolve_report_status`（fail-closed 未知→error）
- **落地偏差 2（T2）**：「维度编造 vs 数值变形」在 DataBinding（无字段语义）下不可区分——行存在性统一归 fabrication 层，numeric 层专司 KPI 聚合重算（伞形三层映射更干净）
- **落地偏差 3（T5+）**：错误码用 `ErrorCode.REPORT_VALIDATION_ERROR.value` 引用而非字面量（单一来源）；violations 决策进 `Tracer.add_decision`（P8 D5 语义复用）
- 验收对照：ReportSpec schema 固化 ✓ / Chart/Table 字段存在于 QueryResult ✓ / KPI 来源可追溯（聚合重算机制）✓ / 不生成不存在的数据（行存在性 + 膨胀检测）✓ / ReportVersion 三态回归 ✓（真 e2e 留 P12 手动门）

## Review-1（2026-08-30 用户实际代码 review，P9+P10 合并审）

**修（3 硬项）**：

| 编号 | 类别 | 修法 | commit |
|---|---|---|---|
| P9-1 | correctness P1 | `_persist()` session 终态参数化（`session_phase` / `last_failed_action`）——原先无条件写 `current_phase='report_ready' + 清 failed_action`，timeout 路径「先 update_phase(error) 后 persist」被覆盖回 report_ready；现 `persist_error_run(session_phase="error", last_failed_action=failed_action)` + fake-pool 契约钉 + timeout 集成断言 | `43a0516` |
| P9-2 | correctness P2 | `_run_confirmed_graph` 加 `graph_completed` 标志，finally 统一 `tracer.end("FAILED")`——原先超时/异常提前退出后 flush 落库的 trace 永远 RUNNING；跑完的 trace 仍由 `_persist_report` end("DONE") 不重复 | `ea8b291` |
| P10-1 | correctness P1 | validator structure 层加行键边界：`set(row.keys()) ⊆ set(data_binding.fields)` + 拒绝空行（`all()` 恒真穿透）——provenance 真正闭合 | （本 commit） |

**记录不修（用户 scope 决定）**：

| 编号 | 内容 | 去向 |
|---|---|---|
| P9-3 | `RETRY_BUDGETS` 是 documented contract 非 executable single source（三处实现各自读配置，测试钉一致）——面试话术：「跨组件一致性契约，测试钉住不能漂移」，不说「由表驱动」 | [[memory:p9-review-findings]] |
| P9-4 | ErrorCode canonicalization incomplete：`classify_sql_kind/kind_to_error_code` 已定义无生产调用点；SQL producer 仍发 legacy `code="EXECUTION_ERROR"`（sql_graph.py:563/570/666） | 同上 |
| P9-5 | generic exception 出口 `message=str(exc)[:300]` 原始异常文本直达用户——码/recoverable 已走 envelope 但文案未走 user_message | 同上 |
| P10-2 | 「ReportSpec v2」指 schema 契约版本，payload `version="1.0"` 是域版本——spec.py docstring 已澄清，勿混 | 同上 |
| P10-3 | KPI 校验当前是全表 aggregate 语义；P11 若支持 filter/subset KPI 必须升级为 subset aggregate，否则合法 KPI 会误判 | 同上 |

## Context

### 伞形 §392 P10 验收清单
- [ ] ReportSpec schema 固定并通过校验
- [ ] Chart/Table 字段存在于 QueryResult
- [ ] KPI 来源可追溯
- [ ] 不生成不存在的数据
- [ ] ReportVersion 创建/查看/切换/重新生成正常（现状已具备——P10 回归钉不回退）

### 契约要求（§十二 + report-runtime.md §二）
三层 Validator，校验对象是 **`ReportSpec → QueryResult` 映射**（不是渲染产物）：
1. **结构校验**：chart field 来自 query result、table column 存在、KPI field 存在
2. **数值校验**：结构化 ReportSpec 中数值必须来源于 Query Result 或明确允许的 aggregation/arithmetic
3. **禁止自由生成**：ReportSpec 是结构化对象而非自由 Markdown；schema 层面不提供自由数据块类型

**明确不做对最终 HTML/文本的正则数字审计**（`12345 / 12,345 / 1.23万` 误报海）。

### 现状盘点（对照 p9-reliability 分支 HEAD）
| 要素 | 现状 | 差距 |
|---|---|---|
| ReportSpec | `models/contracts.py:ReportSpec{version, components, insight}` + `ComponentSpec.data_binding: dict`（自由 dict，无 schema） | 无 provenance 表达；九类 block 未齐 |
| Report Agent | `agent/report_graph.py` 三节点；`chart_config` 来自**确定性** `chart_advisor`（`config.data` = QueryResult rows 原样引用，`dimensions` 指向列名）；insight 来自确定性统计工具文本；LLM 只选工具（assemble_plan） | 真实性靠实现巧合，无 Validator 钉住 |
| answer 装配 | `confirmed_execution_graph.py:_build_output`（line 330-385）：`answer.table` 直通 QueryResult rows；`answer.chart = rs.chart_config`；`answer.insight = rs.insight` | 装配逻辑散在父图，无域归属 |
| 三态落库 | `report_version_service.py` 四 persist_* append-only 在位 | 状态映射语义（三态 → status='done'/'error'）无域定义 |
| 前端契约 | `reportAdapter.ts` 消费 `answer.{insight,text,table,chart}`；chart 依赖 `config.data` + `dimensions.{category\|x}/{value\|y}` | P10 **不动**（P11 才是 Frontend phase） |

### 关键设计事实
1. **真实性现状靠巧合**：数值全部出自确定性工具（chart_advisor / insight_analyst 是代码不是 LLM）——但没有任何层**钉住**这一点；report_graph 未来演化（如 LLM 直接生成 config.data）会静默破坏数据真实性原则。
2. **table 不在 ReportSpec 里**：answer.table 由父图直通装配，ReportSpec 对它零表达——「结构校验：table column 存在」无处校验。
3. **前端渲染契约（answer 形状）P10 不能动**：`QUERY_*` 同款教训（P9）——P11 才是 Frontend/SSE phase。
4. P9 已落 `ErrorCode.REPORT_VALIDATION_ERROR`——码表就位等生产者挂接。

## Design

### D1 `app/report/` 域包（伞形 §二·二目标形状，三件套）

```text
backend/app/report/
├── __init__.py     # 空
├── spec.py         # ReportSpec v2 schema：九类 block 中的结构化子集 + provenance 表达
├── validator.py    # 三层 Validator：validate_report_spec(spec, query_result) -> SpecValidationResult
└── versioning.py   # ReportVersion 域语义：三态 → 存储状态映射 + append-only 不变量
```

**边界**（report-runtime.md §四）：`agent/report_graph.py` = Agent 怎么决定报告结构（不动、不迁目录）；`app/report/` = 报告 Domain Object 怎么定义、校验、版本化。

### D2 spec.py——ReportSpec v2（扩展不重写，向后兼容）

保持 `models/contracts.py:ReportSpec` 类名与既有字段（version/components/insight——已落库 payload 兼容），**扩展**：

```python
class KpiSpec(BaseModel):
    label: str                     # 展示名（"总销售额"）
    field: str                     # QueryResult 列名（来源锚点）
    aggregation: Literal["sum", "avg", "min", "max", "count"] = "sum"
    value: float | int | None = None   # build_output 重算填充；validator 校验 == agg(rows)

class TableSpec(BaseModel):
    columns: list[str]             # ⊆ QueryResult 列名（结构层校验面）

class ComponentSpec(扩展):         # data_binding dict → 结构化
    data_binding: DataBinding

class DataBinding(BaseModel):
    source: Literal["query_result"] = "query_result"
    fields: list[str] = []         # chart 维度/值字段；结构层校验 ⊆ columns
    rows: list[dict] = []          # chart 数据行（数值层校验：逐值 ∈ QueryResult.rows）

class ReportSpec(扩展):
    kpi: list[KpiSpec] = []
    table: TableSpec | None = None
```

- `models/contracts.py` 变 re-export shim（`from app.report.spec import ...`，P9 llm_resilience shim 先例）——contracts.py 既有 import 面（report_graph / 前端 TS 类型无关）不断
- **九类 block 不全上**（title/summary/section/recommendation/alert 无数据面、渲染无消费方）：YAGNI，P11 前端要渲染时再扩；kpi/table/chart/insight 四类覆盖验收全部校验面
- **insight 保持文本字段**：它是确定性统计工具的叙述输出，按伞形「校验对象是结构化映射」不入数值审计（明确不做文本正则）

### D3 validator.py——三层校验单一入口

```python
class SpecViolation(BaseModel):
    layer: Literal["structure", "numeric", "fabrication"]
    block: str          # "kpi[0]" / "chart" / "table"
    field: str = ""
    detail: str

class SpecValidationResult(BaseModel):
    ok: bool
    violations: list[SpecViolation] = []

def validate_report_spec(spec: ReportSpec, query_result: QueryResult) -> SpecValidationResult
```

| 层 | 校验 |
|---|---|
| structure | `kpi[].field ∈ columns`；`table.columns ⊆ columns`；`components[].data_binding.fields ⊆ columns`（chart 维度/值字段存在于 QueryResult） |
| numeric | `kpi[].value == agg(rows[field], aggregation)`（浮点容差 rel 1e-9；aggregation 由 validator 重算——**来源可追溯**）；`data_binding.rows` 每行逐值等于 QueryResult.rows 中同键行（允许子集引用，禁止变形） |
| fabrication | `data_binding.rows` ⊆ QueryResult.rows（行数与内容不得超出）；`table.columns` 非空时行数据必须直通（TableSpec 不携带自由行数据——schema 层禁止）；spec 中不存在 schema 外的自由数据块类型（结构上封死） |

- **fail 语义**：violations 非空 → caller 决定 verdict（本 plan 接线为 FAILED + `REPORT_VALIDATION_ERROR`）——Validator 不自己改 spec、不静默丢弃块（报错优先于修复，错误是 first-class）
- QueryResult 为 None / rows 为空（EMPTY verdict 前已拦截）时 validator 直接 `ok=True` 短路——EMPTY/FAILED 路径不进三层校验

### D4 versioning.py——三态映射语义收编

```python
def resolve_report_status(execution_status: str) -> Literal["done", "error"]:
    """SUCCESS/EMPTY → 'done'；FAILED → 'error'；未知 → 'error'（fail-closed）"""

def resolve_verdict(execution_status: str) -> Literal["SUCCESS", "EMPTY", "FAILED"]:
    """父图 execution_status → 三态 verdict 的单一映射"""
```

- `report_version_service.py` 消费 `resolve_report_status`（`report_status=...` 的三处内联字面量换源，行为不变）
- append-only 不变量钉：同 session version 单调递增 + 三态全部落行（既有 `test_sql_error_envelope.py` 三态路由钉 + 新钉 versioning 纯函数）
- **不做**：`report_version_service.py` 搬家（services/ 是跨域应用服务边界，落库实现留原位——伞形 §465 只说「扩展」）

### D5 接线——Agent 图消费 Validator

1. `report_graph._build_output`：构造 v2 ReportSpec——`kpi`（从 requirement 无 KPI 诉求时不造空块：**P10 kpi 留空列表**，schema+validator 就位即验收「KPI 来源可追溯」的机制面；无渲染消费方时不生产数据）、`table`（columns 直通）、chart component 的 `data_binding{fields, rows=chart_config.config.data}`
2. `confirmed_execution_graph._build_output`（父图装配处）：SUCCESS 路径插入 `validate_report_spec(spec, qr)`；`ok=False` → verdict 改 FAILED + `error=ErrorDetail(code="REPORT_VALIDATION_ERROR", kind="other", message=violations 摘要)` → 走既有 FAILED 全链（persist_error_run + `_build_sse_error` → 用户码 `QUERY_FAILED` 兜底、前端零改动）
3. trace：violations 进 `add_decision`（P8 已有方法）——决策可观察

## Files to change

| 路径 | 变更 |
|---|---|
| `backend/app/report/__init__.py` | 新建（空） |
| `backend/app/report/spec.py` | 新建：ReportSpec v2 + KpiSpec/TableSpec/DataBinding；contracts 类迁入 |
| `backend/app/report/validator.py` | 新建：三层 validate_report_spec + SpecViolation/SpecValidationResult |
| `backend/app/report/versioning.py` | 新建：resolve_report_status / resolve_verdict |
| `backend/app/models/contracts.py` | ReportSpec/ComponentSpec 相关类变 re-export shim |
| `backend/app/agent/report_graph.py` | `_build_output` 构造 v2 spec（provenance 填充） |
| `backend/app/agent/confirmed_execution_graph.py` | `_build_output` SUCCESS 路径插 validator 接线 + FAILED 转化 |
| `backend/app/services/report_version_service.py` | 状态字面量换 `report.versioning.resolve_report_status`（行为不变） |
| `backend/tests/contracts/test_report_spec.py` | 新建：v2 schema 校验/兼容（旧 payload 形状仍可 parse） |
| `backend/tests/contracts/test_report_validator.py` | 新建：三层逐条红路 + 合法绿路 + EMPTY 短路 |
| `backend/tests/contracts/test_report_versioning.py` | 新建：三态映射 + fail-closed 未知值 |
| `backend/tests/graphs/test_report_validator_wiring.py` | 新建：report_graph 产出可过 validator（真图闭环）+ 父图 violations→FAILED 接线 |
| `docs/plans/2026-08-30-p10-report-validator.md` | 本 plan |
| `docs/plans/README.md` | 登记索引 |
| `CLAUDE.md` | §10 现状行更新 |

## Reused existing utilities

| 复用 | 路径 |
|---|---|
| ErrorEnvelope 码表 | `app/reliability/errors.py:ErrorCode.REPORT_VALIDATION_ERROR` —— P9 就位 |
| 兼容 shim 先例 | P9 `llm_resilience.py` / P3 `app/context` facade |
| 三态落库 | `services/report_version_service.py` 四 persist_* —— 只换源状态字面量 |
| 确定性工具 | `chart_advisor` / `insight_analyst` —— 不动，validator 钉其输出契约 |
| SSE 错误链 | `_build_sse_error` + persist_error_run —— REPORT_VALIDATION 走 FAILED 既有全链 |
| Decision trace | `Tracer.add_decision`（P8）—— violations 可观察 |

## Verification

```bash
# 新增单测（TDD 逐任务）
cd backend && pytest tests/contracts/test_report_spec.py tests/contracts/test_report_validator.py tests/contracts/test_report_versioning.py tests/graphs/test_report_validator_wiring.py -v

# 既有交界回归（三态落库 / report_graph / 父图装配）
cd backend && pytest tests/test_sql_error_envelope.py tests/contracts/test_prompt_repair_context.py -v

# 前端零改动证明
cd frontend && npm run test:run

# 全量回归（P9 后基线 891 passed / 1 skipped）
cd backend && pytest --tb=short -q
```

**冒烟矩阵**：
1. 合法 spec（chart data 直通 rows + table.columns ⊆ columns）→ ok=True
2. chart 维度字段不存在 → structure violation；kpi.field 不存在 → structure violation
3. kpi.value 与 agg 重算不符 → numeric violation；data_binding.rows 数值变形 → numeric violation
4. data_binding.rows 携带 QueryResult 之外行 → fabrication violation
5. EMPTY（rows=[]）/ qr=None → 短路 ok=True（不进三层）
6. 父图接线：violations → execution_status=FAILED + error.code=REPORT_VALIDATION_ERROR → persist_error_run + SSE QUERY_FAILED（用户码兜底，前端零改动）
7. report_graph 真图闭环产出 spec 可过 validator（真实性从巧合变契约）
8. 全量 891 基线不回退 + 前端 vitest 不回退

## Explicitly NOT doing

| 不做 | 理由 |
|---|---|
| 迁 `agent/report_graph.py` → `agents/report/` | 纯目录移动零行为收益，牵连 import/trace 面；report-runtime §五「强化决策」留给 P11 前端收口时一并评估 |
| 改前端 `reportAdapter.ts` / 任何渲染面 | answer 形状是前端已消费契约（P9 QUERY_* 教训）；P11 才是 Frontend phase |
| 九类 block 全量 schema | title/summary/section/recommendation/alert 无数据面无消费方——YAGNI，P11 按渲染需求扩 |
| insight 文本数值正则审计 | 伞形 §十二 明确不做（误报海）；insight 是确定性工具叙述输出 |
| KPI 生产（造 kpi 块） | 现状无 KPI 业务诉求与渲染消费方——schema+validator 机制面就位即验收；不生产空块 |
| HTML 正则审计 | 宪法 §10 冻结 |
| `report_version_service.py` 搬家 | services/ 是跨域应用服务边界；伞形 §465 只要求扩展 |
| adaptive repair on validation fail | 数值校验失败直接 FAILED（用户可见稳定错误）；repair 循环属 Execution Agent 职责（P8 边界） |
| Langfuse 落库 | P13 |

## TDD Tasks

### T1 report/spec.py——v2 schema + contracts shim
- [ ] Step1 `test_report_spec.py` 红：v2 字段（kpi/table/data_binding 结构化）parse & dump；旧形状（data_binding={}/缺省）仍可 parse（向后兼容钉）；contracts shim 同一对象 identity
- [ ] Step2 实现至绿

### T2 validator.py——三层
- [ ] Step1 `test_report_validator.py` 红：冒烟矩阵 1-5 逐条
- [ ] Step2 实现至绿（agg 重算 + 行包含性判定纯函数化）

### T3 versioning.py——三态映射
- [ ] Step1 `test_report_versioning.py` 红：SUCCESS/EMPTY→done、FAILED→error、未知→error（fail-closed）
- [ ] Step2 实现 + `report_version_service.py` 三处字面量换源，既有三态钉回归

### T4 report_graph 产出 v2 spec
- [ ] Step1 `test_report_validator_wiring.py` 红：monkeypatch 工具跑真图 → spec 含 data_binding.fields/rows + table.columns，且 validate_report_spec(spec, qr).ok is True
- [ ] Step2 `_build_output` 构造 v2 至绿

### T5 父图接线——violations → FAILED
- [ ] Step1 wiring 测试红：注入 fabricated rows（假 chart_config）→ execution_status=FAILED + error.code=REPORT_VALIDATION_ERROR + persist_error_run 被调
- [ ] Step2 父图插校验 + 错误转化至绿

### T6 收尾
- [ ] Step1 全量后端 891 基线不回退 + 前端 vitest 不回退
- [ ] Step2 CLAUDE.md §10 现状行 + plan 已完成 + 索引迁移
- [ ] Step3 git commit（`feat(p10): ... + plan: p10-report-validator`）

## 预算影响预估

新增测试约 22–26 例（spec ~5 / validator ~10 / versioning ~4 / wiring ~4）；基线 891 → 预计 ~915。

## Open questions

1. **violations → FAILED 是否过重？** 备选：丢弃违规块降级渲染（SUCCESS + 缺块）。拍板：FAILED——「永不伪造成功」（宪法 §10）；部分可用的报告比诚实的失败更危险。待用户 review 确认。
2. KPI 只落机制不生产（D5-1）——若用户要求真 KPI 生产，需先给业务诉求样例。
