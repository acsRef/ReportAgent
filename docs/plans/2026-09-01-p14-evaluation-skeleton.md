# P14 Evaluation 骨架 + 行为期望扩展

> 状态: 进行中（2026-09-01；接 P13 master `079dd2f`；先骨架 + 行为期望；后续 P14b（baseline/optimized 对比）/ P14c（regression loop）按需串行）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `evaluation/` 从单根结构升级为伞形 plan §十四目录终态的 9 子包（`requirement / memory / retrieval / tool_selection / sql / repair / report / frontend / e2e`）；把 `evaluation/checker.py` 升级为可注册 dispatcher，让 `BehaviorExpectation`（memory_required / retrieval）从「deferred 占位」升级为「可被各子包注册判定」；为每个非占位子包提供一个 example case 复盘现有 baseline 数据，不增加 baseline categories（现状 13 类已超出伞形 11 类）。

**Architecture:** 9 子包 DIM_REGISTRY 全部注册（`requirement / memory / retrieval / tool_selection / sql / repair / report / frontend / e2e`），Phase 1 legacy 4 段（`requirement / execution / report / behavior`）保留为 backward-compatible source of truth，Phase 2 dispatcher 仅 active 7 dim：`memory / retrieval / tool_selection / sql / repair / frontend / e2e`（`requirement` / `report` 注册进 registry 但 Phase 2 跳过以免与 Phase 1 legacy section key 重复）。各子包 `assert_<dim>(obs, exp) -> (sections, deferred_keys)` 函数；placeholder 子包（memory / retrieval / tool_selection / repair）函数体返回 `({}, list(exp.keys()))` 表示「schema 已定、observation 尚未接入（P14b）」的真正 deferred；frontend / e2e 是真 no-op（无 expectation schema），返回 `({}, [])`。

**Tech Stack:** Python 3.11 + Pydantic v2（schema 已用）+ pytest（unit + 集成 smoke）+ `evaluation/runner.py` P0 baseline runner（升级 dim_results output）

---

## Context

伞形 plan §十四（2026-08-25 冻结）规定 P14 阶段必须交付：目录终态 `evaluation/{requirement, memory, retrieval, tool_selection, sql, repair, report, frontend, e2e}/`、Golden Set ≥ 20、自动 Evaluation、Agent/SQL/Report/Memory 指标齐、Regression detection、baseline/optimized 对比。本 plan 是「骨架 + 行为期望」阶段（用户 2026-09-01 决策：「先骨架 + 行为期望（最小可跑）」）。P14b（baseline/optimized 对比 + Langfuse trace 真接入）/ P14c（regression loop）按需串行。

`evaluation/` 当前单根结构（`baseline_cases.json` 20 例 / `checker.py` 156 行 / `loader.py` / `runner.py` 333 行 / `schema.py` 90 行 + `tests/` 5 文件）已具备 requirement / execution / report 三段判定与 behavior 段 4 键（memory_required / memory_types / retrieval / clarification）—— 后三者在 `checker.py:119-121` 全部 deferred 到 P13 Langfuse 后才可观测。本 plan 落地后 memory / retrieval 的 dispatcher hook 就位，但内部实际查 Langfuse trace 的代码留 P14b。

**用户 2026-09-01 决策**：

| 维度 | 决策 |
|---|---|
| 双模型 plan | 搁置（pipeline 兼容 MiniMax；P14 起步期间不交叉模型维度评估） |
| P14 粒度 | 先骨架 + 行为期望（最小可跑） |
| P15 起点 | 独立 plan，先 README + ADR 框架 |
| P14/P15 plan 关系 | 独立两 plan，串行 |

### 复用现有基线（不重写）

| 资产 | 路径 | 不动原因 |
|---|---|---|
| `BaselineCase` Pydantic schema | `evaluation/schema.py:59-90` | 现有 20 例已通过，扩字段会破坏 schema freeze |
| `BehaviorExpectation` | `evaluation/schema.py:21-27` | 现有 4 键在 P0 plan 中已签 schema freeze |
| `RequirementExpectation` | `evaluation/schema.py:30-35` | legacy 段保留 |
| `ExecutionExpectation` | `evaluation/schema.py:38-42` | legacy 段保留 |
| `ReportExpectation` | `evaluation/schema.py:45-48` | legacy 段保留 |
| `check_turn` legacy 4 段 | `evaluation/checker.py:45-122` | P0-P12 已有 5 个 test_*.py 依赖其输出 section key |
| `summarize` | `evaluation/checker.py:126-156` | runner.py 依赖 |
| `run_case` / `_observe_turn` | `evaluation/runner.py:84-241` | P12 manual gate，签名稳定 |
| 20 例 baseline data | `evaluation/baseline_cases.json` | 现状 13 categories 已超伞形 11 类；plan 内**不增加 categories** |

## Design

### 1. 目录终态

```
evaluation/
├── __init__.py                       # 既有
├── baseline_cases.json               # 既有（不改）
├── checker.py                        # 升级：加 dispatcher + legacy 4 段保留
├── loader.py                         # 既有（不改）
├── runner.py                         # 升级：output 加 dim_results
├── schema.py                         # 不改 Pydantic 模型（freeze）
├── results/                          # 既有（不改）
├── tests/                            # 既有（test_checker / test_dataset / test_report_render / test_runner_integration / test_schema）
├── requirement/                      # NEW：实装（status / target_metrics / time_range / missing_fields）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── memory/                           # NEW：实装 dispatcher hook（deferred；P14b 接 Langfuse）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── retrieval/                        # NEW：实装 dispatcher hook（deferred；P14b 接 Langfuse）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── tool_selection/                   # NEW：实装 dispatcher hook（最小）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── sql/                              # NEW：实装 dispatcher hook（最小；复用 execution legacy 判定）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── repair/                           # NEW：占位（与 sql 子包判空共用 dispatcher 注册）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── report/                           # NEW：实装（KPI / Table / Chart 字段溯源）
│   ├── __init__.py
│   ├── harness.py
│   └── tests/test_harness.py
├── frontend/                         # NEW：占位（P11 已落地，前端契约稳定不重测）
│   ├── __init__.py
│   └── harness.py                    # 空函数（no-op）
└── e2e/                              # NEW：占位（P12 已 Playwright，evaluation e2e = 现有 runner）
    ├── __init__.py
    └── harness.py                    # 空函数（no-op）
```

### 2. Dispatcher 协议

**D1 边界（设计评审后定稿）**：

- **DIM_REGISTRY 注册 9 个 dim**：`requirement / memory / retrieval / tool_selection / sql / repair / report / frontend / e2e`
- **Phase 1 legacy 唯一负责**：`requirement / execution / report / behavior`（需求 / 执行 / 报告 / 行为）4 段判定——`requirement` 与 `report` 虽注册进 DIM_REGISTRY，但 Phase 2 dispatcher 跳过（避免与 Phase 1 section key 重复）
- **Phase 2 active dispatch**：`memory / retrieval / tool_selection / sql / repair / frontend / e2e` 7 个 dim
- **`execution` / `behavior` 不在 DIM_REGISTRY**：它们永远走 Phase 1 legacy（无 Phase 2 替代）

`LEGACY_KEYS = {"requirement", "execution", "report", "behavior"}` 用于 Phase 2 dispatcher 跳过；此 set 与 DIM_REGISTRY 交集 = `{requirement, report}`，这两个 dim 注册但不 active dispatch，是「为后续统一 9-dim dispatcher 留的兼容注册位」。

**D2 边界（设计评审后定稿）**：placeholder 子包（memory / retrieval / tool_selection / repair）必须返回 `(sections, deferred_keys)` 其中 `deferred_keys == list(exp.keys())`，让 `dim_results` 把每个 expected key 计入 `deferred`，用户才能在 result 里看到「schema 已定义、observation 尚未接入」的语义。frontend / e2e 是真 no-op（无 expectation schema），返回 `(sections=[], deferred_keys=[])`,不计入 deferred（它们的 `dim_results[dim] = {pass:0, fail:0, deferred:0}` 是预期的，不是缺漏）。

**`evaluation/checker.py` 中新增**：

```python
DIM_REGISTRY: dict[str, Callable[[ObservedTurn, dict], tuple[dict[str, str], list[str]]]] = {}
```

`check_turn(observed, expectation)` 升级算法（伪）：

```python
def check_turn(observed, expectation):
    sections = {}
    deferred = []
    exp = expectation or {}
    # Phase 1：legacy 4 段唯一负责（requirement/execution/report/behavior）
    _check_requirement_legacy(observed, exp.get("requirement") or {}, sections, deferred)
    _check_execution_legacy(observed, exp.get("execution") or {}, sections, deferred)
    _check_report_legacy(observed, exp.get("report") or {}, sections, deferred)
    _check_behavior_legacy(observed, exp.get("behavior") or {}, sections, deferred)
    # Phase 2：active dispatch 仅 7 dim（memory/retrieval/tool_selection/sql/repair/frontend/e2e）
    # requirement/report 注册但被 LEGACY_KEYS 跳过——见 D1 边界
    LEGACY_KEYS = {"requirement", "execution", "report", "behavior"}
    for dim, fn in DIM_REGISTRY.items():
        if dim in LEGACY_KEYS:
            continue
        if dim in exp and isinstance(exp[dim], dict):
            dim_sections, dim_deferred = fn(observed, exp[dim])
            sections.update({f"{dim}.{k}": v for k, v in dim_sections.items()})
            deferred.extend([f"{dim}.{k}" for k in dim_deferred])
    return sections, deferred
```

子包 harness 函数签名：

```python
# evaluation/requirement/harness.py
from evaluation.checker import ObservedTurn

def assert_requirement(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """RequirementCard 字段级判定。

    exp 示例：
      {"status": "complete", "min_missing_fields": 1,
       "time_range_equals": "2024年",
       "target_metrics_contains": ["销售额"]}
    """
    sections: dict[str, str] = {}
    deferred: list[str] = []
    if exp.get("status") is not None:
        sections["status"] = "pass" if obs.card_status == exp["status"] else "fail"
    if exp.get("min_missing_fields") is not None:
        got = obs.missing_fields_count
        sections["min_missing_fields"] = (
            "pass" if got is not None and got >= exp["min_missing_fields"] else "fail"
        )
    if exp.get("time_range_equals") is not None:
        sections["time_range_equals"] = (
            "pass" if obs.time_range == exp["time_range_equals"] else "fail"
        )
    if exp.get("target_metrics_contains"):
        want_any = exp["target_metrics_contains"]
        hit = any(any(w in m for m in obs.target_metrics) for w in want_any)
        sections["target_metrics"] = "pass" if hit else "fail"
    return sections, deferred
```

子包 `__init__.py` 注册一行：

```python
# evaluation/requirement/__init__.py
from evaluation.checker import DIM_REGISTRY
from . import harness as _harness  # noqa: F401

DIM_REGISTRY["requirement"] = _harness.assert_requirement
```

幂等保护（防止重复 import 重复注册）：

```python
# evaluation/checker.py DIM_REGISTRY 模块级
DIM_REGISTRY: dict[str, ...] = {}

# 子包注册时检查
if dim not in DIM_REGISTRY:
    DIM_REGISTRY[dim] = fn
```

### 3. ObservedTurn 扩展字段（最小）

`evaluation/checker.py` 中 `ObservedTurn` 暂不扩字段（向后兼容）。子包函数接收完整 `ObservedTurn`，memory / retrieval 子包在 P14b 阶段才需要 `langfuse_trace: list[dict]` 之类的新字段；届时按 Pydantic 兼容模式（默认 None）增量扩。

### 4. Runner 输出扩展

`evaluation/runner.py:226-232` result dict 加 `dim_results`：

```python
dim_results = {}
for dim, fn in checker.DIM_REGISTRY.items():
    # 找到该 dim 在本 case expectations 里被引用的所有 turn 的 merged 结果
    ...
result = {
    "case_id": case.id,
    ...
    "sections": sections_all,            # 既有（含 legacy + dim.*）
    "dim_results": dim_results,          # 新增：dim → {pass: n, fail: n, deferred: n}
    "deferred": sorted(set(deferred_all)),
    ...
}
```

`dim_results` 格式：

```python
dim_results[dim] = {
    "pass": sum(1 for v in section_keys_for_dim if v == "pass"),
    "fail": sum(... == "fail"),
    "deferred": len(deferred_keys_for_dim),
}
```

注：`status: "fail" if any(v.startswith("fail") for v in sections_all.values()) else "pass"` 保持不变 —— dim 结果是元数据，不影响 status 聚合。

### 5. 行为期望升级示意（baseline 数据**不动**，新的 dim key 在 P14b 阶段用）

```json
{
  "id": "memory-recall-multiturn",
  "category": "multi_turn",
  "description": "第2轮依赖会话上下文 → memory.recalled 应为 true（P14b 接入 Langfuse 后才可观测）",
  "turns": [
    {"query": "2024年各区域销售额", "mode": "new"},
    {"query": "再按产品细分", "mode": "supplement"}
  ],
  "expectations": [
    {},
    {
      "requirement": {"status": "complete"},
      "execution": {"verdict": "SUCCESS", "sql_nonempty": true},
      "memory": {"recalled": true, "types_any_of": ["conversation", "session"]},
      "behavior": {"clarification": false}
    }
  ]
}
```

`memory` 键走 dispatcher；`behavior` 键走 legacy 4 段（disambiguation 由 DIM_REGISTRY 名字区分）；互不冲突。

### 6. 不动现状

- baseline_cases.json：schema、categories、20 例数据**完全不动**。每子包 1 example case 用独立的测试文件（`evaluation/<dim>/tests/test_harness.py` 中以 ObservedTurn fixture 形式提供），不污染主 baseline 数据。
- `evaluation/schema.py` Pydantic 模型：**不动**（plan 边界外）。
- `evaluation/runner.py`：只加 `dim_results` 字段，老用法不变。

## Files to change

| 模式 | 路径 | 内容概要 |
|---|---|---|
| 新建 | `docs/plans/2026-09-01-p14-evaluation-skeleton.md` | 本文件 |
| 新建 | `evaluation/requirement/__init__.py` + `harness.py` + `tests/test_harness.py` | 4 类期望判定函数 + 单元测试 |
| 新建 | `evaluation/memory/__init__.py` + `harness.py` + `tests/test_harness.py` | dispatcher hook + deferred 占位 |
| 新建 | `evaluation/retrieval/__init__.py` + `harness.py` + `tests/test_harness.py` | dispatcher hook + deferred 占位 |
| 新建 | `evaluation/tool_selection/__init__.py` + `harness.py` + `tests/test_harness.py` | dispatcher hook（最小） |
| 新建 | `evaluation/sql/__init__.py` + `harness.py` + `tests/test_harness.py` | dispatcher hook（最小；复用 execution 数据） |
| 新建 | `evaluation/repair/__init__.py` + `harness.py` + `tests/test_harness.py` | dispatcher hook（占位） |
| 新建 | `evaluation/report/__init__.py` + `harness.py` + `tests/test_harness.py` | 3 类期望判定函数（KPI / Table / Chart 字段） |
| 新建 | `evaluation/frontend/__init__.py` + `harness.py` | 空 no-op（仅注册） |
| 新建 | `evaluation/e2e/__init__.py` + `harness.py` | 空 no-op（仅注册） |
| 修改 | `evaluation/checker.py` | 加 `DIM_REGISTRY` + `check_turn` 内 Phase 2 dispatch + 模块级 import 9 子包实现注册 |
| 修改 | `evaluation/runner.py` | output `result` dict 加 `dim_results` 字段；汇总逻辑从 sections_all |
| 修改 | `docs/plans/README.md` | 已经 admin 更新（双模型移入暂缓 + P14 登记） |

## Reused existing utilities

- `evaluation/checker.py:33-41` `_derive_verdict`（SUCCESS/EMPTY/FAILED 三态）—— **不重写**
- `evaluation/checker.py:14-30` `ObservedTurn` Pydantic 模型 —— **不动**
- `evaluation/checker.py:45-122` `check_turn` legacy 4 段判定 —— **保留**，改名为模块内私有 helper
- `evaluation/checker.py:126-156` `summarize` —— **不动**
- `evaluation/schema.py:1-90` 全部 Pydantic 模型 —— **不动**（schema 冻结）
- `evaluation/runner.py:34-158` SSE 驱动 + `_observe_turn` —— **不动**
- `evaluation/runner.py:161-241` `run_case` —— **只**改 result dict 字段，**算法不动**
- `evaluation/loader.py` —— **不动**
- `evaluation/tests/test_checker.py` + `test_schema.py` + `test_dataset.py` —— **不动**（回归基线）
- P13 Langfuse 资产 `backend/app/observability/langfuse_flush.py` —— P14b 阶段复用，本 plan 不引
- P12 Mock LLM Adapter `backend/app/llm/mock.py` —— P14b/c 阶段，本 plan 不引

## Verification

### 单测 + 子包测试

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/ -v
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/requirement/tests/ evaluation/memory/tests/ evaluation/retrieval/tests/ evaluation/tool_selection/tests/ evaluation/sql/tests/ evaluation/repair/tests/ evaluation/report/tests/ -v
```

预期：现有 5 个 test_*.py 全绿 + 7 个子包 test_harness.py 全绿 = **至少 12 个测试文件 PASS**

### 后端零回归

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
```

预期：**990+ passed / 1 skipped / 5 warnings**（零回归）

### Dispatcher smoke

```bash
cd backend && D:/miniConda/envs/agent/python.exe -c "
from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY, build_dim_results

# 1. DIM_REGISTRY 9 个 dim 全部注册（含 requirement / report 等兼容注册位）
assert len(DIM_REGISTRY) == 9, f'期望 9 dim 实注册, 实际 {len(DIM_REGISTRY)}'

# 2. LEGACY_KEYS dispatch skip（D1 边界）：requirement 不会经过 Phase 2 dispatcher
obs = ObservedTurn(card_status='complete', target_metrics=['销售额'], time_range='2024年', sql='SELECT 1', row_count=10)
sec, _ = check_turn(obs, {
    'requirement': {'status': 'complete', 'target_metrics_contains': ['销售额'], 'time_range_equals': '2024年'},
    'execution': {'verdict': 'SUCCESS', 'sql_nonempty': True},
})
assert sec['requirement.status'] == 'pass'
assert sec['execution.verdict'] == 'pass'
requirement_keys = [k for k in sec if k.startswith('requirement.')]
assert len(requirement_keys) == 1, f'Phase 1/2 应只写一份，got {requirement_keys}'

# 3. active dispatch dim（sql 实装）→ sections 写 sql.* prefix
sec2, _ = check_turn(obs, {'sql': {'sql_nonempty': True, 'rows_gt': 5}})
assert 'sql.sql_nonempty' in sec2 and sec2['sql.sql_nonempty'] == 'pass'

# 4. deferred dim（memory 占位）→ sections 空 + deferred 包含 exp keys（D2 边界）
sec3, deferred3 = check_turn(obs, {'memory': {'recalled': True, 'types_any_of': ['conversation']}})
assert not any(k.startswith('memory.') for k in sec3.keys())
assert 'memory.recalled' in deferred3 and 'memory.types_any_of' in deferred3

# 5. 真 no-op dim（frontend）→ sections 空 + deferred 也空（无 expectation schema）
sec4, deferred4 = check_turn(obs, {'frontend': {'any_key': True}})
assert not any(k.startswith('frontend.') for k in sec4.keys())
assert not any(k.startswith('frontend.') for k in deferred4)

# 6. build_dim_results 纯函数——聚合 sections + deferred → dim_results
sections = {'sql.sql_nonempty': 'pass', 'sql.rows_gt': 'pass',
            'requirement.status': 'pass', 'report.table_present': 'pass'}
deferred = ['memory.recalled', 'memory.types_any_of']
dim_results = build_dim_results(sections, deferred, ['sql', 'memory', 'requirement', 'report', 'frontend'])
assert dim_results['sql'] == {'pass': 2, 'fail': 0, 'deferred': 0}
assert dim_results['memory'] == {'pass': 0, 'fail': 0, 'deferred': 2}
assert dim_results['requirement'] == {'pass': 1, 'fail': 0, 'deferred': 0}
assert dim_results['report'] == {'pass': 1, 'fail': 0, 'deferred': 0}
assert dim_results['frontend'] == {'pass': 0, 'fail': 0, 'deferred': 0}
print('D1 + D2 + dispatcher + build_dim_results 全 OK')
"
```

### 9 子包目录在场

```bash
ls evaluation/requirement/harness.py evaluation/memory/harness.py evaluation/retrieval/harness.py evaluation/tool_selection/harness.py evaluation/sql/harness.py evaluation/repair/harness.py evaluation/report/harness.py evaluation/frontend/harness.py evaluation/e2e/harness.py 2>/dev/null | wc -l
```

预期：`9`

### build_dim_results 纯函数

已被 `evaluation/tests/test_checker_dim_results.py` 6 例独立覆盖（Task 3 Step 3.1）：
- pass section 计数
- fail section startswith 计数
- deferred key 计数
- 未知 dim 默认零值
- 边界 prefix（避免 `sqltable.foo` 误归 `sql`）
- LEGACY_KEYS 4 dim 一并支持

## Explicitly NOT doing

- **不重写** baseline_cases.json 20 例（schema / 数据 / categories 全不动）
- **不增加** baseline categories 数量（现状 13 类已超出伞形 11 类）
- **不实现** LLM judge（Report factuality 走 P10 KPI Validator 三层校验）
- **不接入** Langfuse SDK 直接查询（adapter 解耦保持；trace 数据通过 ObservedTurn 扩字段间接传入；P14b 实装）
- **不做** baseline/optimized 对比机制（P14b）
- **不做** regression detection 自动化（P14c）
- **不修** Playwright Contract E2E（p12 已 done）
- **不写** subgraph-level harness（sql_graph / requirement_analysis_graph 等单元子图）
- **不实现** memory / retrieval 的 Langfuse 查询逻辑（dispatcher hook 就位；函数体 deferred 占位；P14b 填实际 query 代码）
- **不扩** ObservedTurn 字段（向后兼容；P14b 阶段按需扩）
- **不动** `evaluation/schema.py` 任何 Pydantic 模型（schema 冻结；新 dim key 走 raw dict 路径）
- **不删** `behavior.memory_required` / `behavior.retrieval` legacy 4 段判定（schema 兼容路径）

---

## Tasks

### Task 0: Admin —— 双模型 plan 暂缓 + README 同步

**Files:**
- Modify: `docs/plans/README.md`（已修改：双模型从「进行中」表移到「暂缓」表 + P14 登记）
- Modify: `docs/plans/2026-09-01-llm-dual-model-r1-v3.md`（已修改：状态 → 暂缓 + 重启条件）

- [x] Step 0.1: 双模型 plan 文件状态 + 暂缓理由 已落地
- [x] Step 0.2: README 暂缓表 已加 dual-model entry
- [x] Step 0.3: README 进行中表 P14 entry 已加（Plan 字段写 YYYY-MM-DD-p14-evaluation-skeleton.md）
- [ ] Step 0.4: Commit admin 改动
  ```bash
  git add docs/plans/README.md docs/plans/2026-09-01-llm-dual-model-r1-v3.md
  git commit -m "docs(p14): dual-model plan 暂缓（用户 2026-09-01 决策）+ P14 plan 登记"
  ```

---

### Task 1: `evaluation/checker.py` 加 DIM_REGISTRY + Phase 2 dispatch

**Files:**
- Modify: `evaluation/checker.py:1-5`（加 module-level DIM_REGISTRY + `_register_default_dims()` 函数）

- [ ] **Step 1.1: 写 dispatcher 注册测试（先红）**

新建 `evaluation/tests/test_dispatcher.py`：

```python
"""Dispatcher 注册与 dispatch 行为测试。

D1 边界：requirement / execution / report / behavior 走 Phase 1 legacy；
         memory / retrieval / tool_selection / sql / repair / frontend / e2e 走 Phase 2 active dispatch。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation


def test_dim_registry_contains_expected_dims():
    """DIM_REGISTRY 9 keys 必须全部注册（含 requirement / report 等兼容注册位）。"""
    from evaluation.checker import DIM_REGISTRY

    expected = {
        "requirement", "memory", "retrieval",
        "tool_selection", "sql", "repair", "report",
        "frontend", "e2e",
    }
    missing = expected - set(DIM_REGISTRY.keys())
    assert not missing, f"missing dims: {missing}"


def test_check_turn_phase1_legacy_compat_requirement():
    """Phase 1 legacy 唯一负责 requirement 段——section key 带 requirement. 前缀。"""
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn(
        card_status="complete",
        target_metrics=["销售额", "订单"],
        time_range="2024年",
    )
    sec, _ = check_turn(obs, {
        "requirement": {
            "status": "complete",
            "target_metrics_contains": ["销售额"],
            "time_range_equals": "2024年",
        }
    })
    assert sec["requirement.status"] == "pass"
    assert sec["requirement.target_metrics"] == "pass"
    assert sec["requirement.time_range_equals"] == "pass"


def test_check_turn_phase2_dispatch_to_sql_dim():
    """Phase 2 active dispatch：sql dim 不在 LEGACY_KEYS，走 assert_sql harness。

    sql 子包实装（D2 实装分支），dispatch 后 section key 带 `sql.` 前缀。
    """
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn(sql="SELECT 1", row_count=10)
    sec, _ = check_turn(obs, {"sql": {"sql_nonempty": True, "rows_gt": 5}})
    assert "sql.sql_nonempty" in sec
    assert sec["sql.sql_nonempty"] == "pass"
    assert "sql.rows_gt" in sec
    assert sec["sql.rows_gt"] == "pass"


def test_check_turn_phase2_dispatch_to_memory_dim_deferred():
    """Phase 2 dispatch：memory dim deferred（D2 占位：sections 空、deferred 包含 expected keys）。"""
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"memory": {"recalled": True, "types_any_of": ["conversation"]}})
    # memory 子包 placeholder 返回 ( {}, list(exp.keys()) )
    # dispatcher 加 'memory.' 前缀到 deferred keys
    assert not any(k.startswith("memory.") for k in sec.keys()), (
        "memory placeholder 不应写 sections"
    )
    assert "memory.recalled" in deferred
    assert "memory.types_any_of" in deferred


def test_check_turn_phase2_skips_requirement_dim():
    """D1 边界：requirement 在 LEGACY_KEYS 内，Phase 2 dispatcher 跳过——只通过 Phase 1 legacy 处理。

    即使用户调用 check_turn 时显式传 requirement 子包函数也只跑一次（Phase 1），不会重复 dispatch。
    """
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn(card_status="complete", target_metrics=["销售额"])
    sec, _ = check_turn(obs, {"requirement": {"status": "complete"}})
    # 仅一项 section（Phase 1 legacy 写入）
    requirement_keys = [k for k in sec if k.startswith("requirement.")]
    assert len(requirement_keys) == 1
    assert "requirement.status" in sec


def test_frontend_noop_returns_empty_not_deferred():
    """frontend 子包真 no-op：无 expectation schema → 不计 deferred（dim_results[frontend] 全 0）。"""
    from evaluation.checker import check_turn, ObservedTurn

    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"frontend": {"any_key": True}})
    assert not any(k.startswith("frontend.") for k in sec.keys())
    assert not any(k.startswith("frontend.") for k in deferred)
```

- [ ] **Step 1.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_dispatcher.py -v
```

预期：FAIL——`test_dim_registry_contains_expected_dims` DIM_REGISTRY 暂未定义；其他 4 个测试也 RED（依 DEPENDS check_turn 改动）

- [ ] **Step 1.3: 在 `evaluation/checker.py` 顶部加 DIM_REGISTRY**

修改 `evaluation/checker.py:1-12`：

```python
"""判定引擎：ObservedTurn（runner 组装的观测）vs TurnExpectation（数据集期望）。

纯函数，不碰网络。原则（冻结基线 §十四）：
- 可观测即判定；不可观测即 deferred（不影响 pass/fail）。
- verdict 推导对齐三态语义 SUCCESS / EMPTY / FAILED。
- P14 升级：Phase 1（legacy 4 段）+ Phase 2（9 子包 dispatcher）。
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field


# P14: 9 子包 dispatcher registry（key = dim 名, value = (obs, exp) -> (sections, deferred))
DIM_REGISTRY: dict[str, Callable[["ObservedTurn", dict], tuple[dict[str, str], list[str]]]] = {}


def register_dim(name: str) -> Callable:
    """子包 harness 注册装饰器（幂等）。"""
    def deco(fn: Callable) -> Callable:
        if name not in DIM_REGISTRY:
            DIM_REGISTRY[name] = fn
        return fn
    return deco
```

- [ ] **Step 1.4: 改造 `check_turn` 加 Phase 2 dispatch**

修改 `evaluation/checker.py:45-122`（整体替换 check_turn 函数体）：

```python
def check_turn(
    observed: ObservedTurn, expectation: dict[str, Any]
) -> tuple[dict[str, str], list[str]]:
    """返回 ({section: pass|fail}, deferred_keys)。任何 fail 即该例 fail。

    Phase 1（legacy）：requirement / execution / report / behavior 4 段（保留 P0 行为）。
    Phase 2（dispatch）：expectation 字典中其它 key 走 DIM_REGISTRY 子包 harness，
      section prefix 为 `<dim>.<key>`，与 legacy section 不冲突。
    """
    sections: dict[str, str] = {}
    deferred: list[str] = []
    exp = expectation or {}

    # ---- Phase 1: legacy 4 段（P0-P12 行为冻结）----
    # requirement
    req = exp.get("requirement") or {}
    if req.get("status") is not None:
        sections["requirement.status"] = (
            "pass" if observed.card_status == req["status"] else "fail"
        )
    if req.get("min_missing_fields") is not None:
        got = observed.missing_fields_count
        sections["requirement.min_missing_fields"] = (
            "pass" if got is not None and got >= req["min_missing_fields"] else "fail"
        )
    if req.get("time_range_equals") is not None:
        sections["requirement.time_range_equals"] = (
            "pass" if observed.time_range == req["time_range_equals"] else "fail"
        )
    if req.get("target_metrics_contains"):
        want_any = req["target_metrics_contains"]
        hit = any(any(w in m for m in observed.target_metrics) for w in want_any)
        sections["requirement.target_metrics"] = "pass" if hit else "fail"

    # execution
    exe = exp.get("execution") or {}
    if exe.get("verdict") is not None:
        derived = _derive_verdict(observed)
        sections["execution.verdict"] = (
            "pass" if derived == exe["verdict"] else
            f"fail(derived={derived})"
        ) if derived != exe["verdict"] else "pass"
    if exe.get("sql_nonempty"):
        sections["execution.sql_nonempty"] = (
            "pass" if bool(observed.sql and observed.sql.strip()) else "fail"
        )
    if exe.get("rows_gt") is not None:
        rc = observed.row_count
        sections["execution.rows_gt"] = (
            "pass" if rc is not None and rc > exe["rows_gt"] else "fail"
        )
    if exe.get("sse_error_code"):
        sections["execution.sse_error_code"] = (
            "pass" if observed.error_code == exe["sse_error_code"] else "fail"
        )

    # report
    rep = exp.get("report") or {}
    if rep.get("table_present") is not None:
        sections["report.table_present"] = (
            "pass" if observed.table_present == rep["table_present"] else "fail"
        )
    if rep.get("chart_present") is not None:
        sections["report.chart_present"] = (
            "pass" if observed.chart_present == rep["chart_present"] else "fail"
        )
    if rep.get("rows_gt") is not None:
        tr = observed.table_rows
        sections["report.rows_gt"] = (
            "pass" if tr is not None and tr > rep["rows_gt"] else "fail"
        )

    # behavior（保留 P0 legacy 4 段；新 dim 走 dispatcher）
    beh = exp.get("behavior") or {}
    if beh.get("clarification") is not None:
        obs_clarify = observed.card_status == "missing"
        sections["behavior.clarification"] = (
            "pass" if obs_clarify == beh["clarification"] else "fail"
        )
    for key in ("memory_required", "memory_types", "retrieval"):
        if beh.get(key) is not None:
            deferred.append(f"behavior.{key}")  # 内部观测 → P14b 升级

    # ---- Phase 2: 9 子包 dispatcher ----
    LEGACY_KEYS = {"requirement", "execution", "report", "behavior"}
    for dim, fn in DIM_REGISTRY.items():
        if dim in LEGACY_KEYS:
            continue  # D1 边界：requirement/report 注册但 Phase 2 跳过
        if dim in exp and isinstance(exp[dim], dict):
            dim_sections, dim_deferred = fn(observed, exp[dim])
            sections.update({f"{dim}.{k}": v for k, v in dim_sections.items()})
            deferred.extend([f"{dim}.{k}" for k in dim_deferred])

    return sections, deferred
```

- [ ] **Step 1.5: 跑 dispatcher 测试确认仍红（缺子包）**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_dispatcher.py -v
```

预期：`test_dim_registry_contains_expected_dims` FAIL（DIM_REGISTRY 为空）+ `test_check_turn_phase2_dispatch_to_sql_dim` FAIL（sql 子包未实装）+ 其余 PASS（只检验 checker.py 内部 dispatcher 逻辑与 legacy skip）

- [ ] **Step 1.6: Commit dispatcher 骨架**

```bash
git add evaluation/checker.py evaluation/tests/test_dispatcher.py
git commit -m "feat(evaluation): checker.py DIM_REGISTRY + Phase 2 dispatcher（legacy 4 段保留）"
```

---

### Task 2: 9 子包占位骨架（frontend / e2e 不写测试，仅骨架）

**Files:**
- Create: 9 个子包目录，每个 `__init__.py` + `harness.py`（frontend / e2e 用 no-op）

- [ ] **Step 2.1: 写目录骨架 test（先红）**

新建 `evaluation/tests/test_subpackage_layout.py`：

```python
"""9 子包目录布局测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.evaluation

EVAL_ROOT = Path(__file__).resolve().parents[2]  # backend/evaluation/tests -> backend/evaluation

EXPECTED_DIMS = [
    "requirement", "memory", "retrieval",
    "tool_selection", "sql", "repair", "report",
    "frontend", "e2e",
]


@pytest.mark.parametrize("dim", EXPECTED_DIMS)
def test_each_dim_has_init_harness(dim):
    pkg = EVAL_ROOT / dim
    assert (pkg / "__init__.py").exists(), f"{dim}/__init__.py 缺失"
    assert (pkg / "harness.py").exists(), f"{dim}/harness.py 缺失"


@pytest.mark.parametrize("dim", EXPECTED_DIMS[:-2])  # 7 个非占位子包
def test_non_noop_dims_have_tests(dir=EXPECTED_DIMS[:-2], dim=None):
    """requirement/memory/retrieval/tool_selection/sql/repair/report 7 个子包需 test_harness.py。"""
    if dim is None:
        return  # noqa
    tests = EVAL_ROOT / dim / "tests" / "test_harness.py"
    assert tests.exists(), f"{dim}/tests/test_harness.py 缺失"
```

- [ ] **Step 2.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_subpackage_layout.py -v
```

预期：FAIL（9 个子目录都不存在）

- [ ] **Step 2.3: 创建 9 个子包目录**

```bash
mkdir -p evaluation/requirement/tests evaluation/memory/tests evaluation/retrieval/tests evaluation/tool_selection/tests evaluation/sql/tests evaluation/repair/tests evaluation/report/tests evaluation/frontend evaluation/e2e
```

- [ ] **Step 2.4: 创建前端/端到端真 no-op 占位（**先写这俩，因为它们最简单**）**

`evaluation/frontend/harness.py`：

```python
"""frontend dim 真 no-op 占位（P11 已落地前端契约冻结；本 plan 不重测）。

D2 边界：frontend 子包无 expectation schema——属于「未定义期望」而非「延后判定」，
因此返回 `(sections=[], deferred_keys=[])`（不是 `list(exp.keys())`）。
dim_results[frontend] = {pass:0, fail:0, deferred:0} 是预期状态，不是缺漏。

未来填点（建议但不实施）：phase 状态机迁移正确性 / EventSource reconnect 行为 /
ProgressCard 真 trace 驱动 fallback 等。本子包作为「前端 P14 评估」hook 占位。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("frontend")
def assert_frontend(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """真 no-op：不在 dim_results 体现 deferred。"""
    _ = obs
    _ = exp
    return {}, []
```

`evaluation/frontend/__init__.py`：

```python
from . import harness as _h  # noqa: F401
```

`evaluation/e2e/harness.py`：

```python
"""e2e dim 真 no-op 占位（P12 Playwright 已 done；evaluation e2e = 现有 runner）。

D2 边界：与 frontend 同——无 expectation schema → 不计 deferred。
未来填点（建议但不实施）：Playwright Full E2E 启动延迟 / report 渲染一致性 /
session resume round-trip 等。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("e2e")
def assert_e2e(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """真 no-op：不在 dim_results 体现 deferred。"""
    _ = obs
    _ = exp
    return {}, []
```

`evaluation/e2e/__init__.py`：

```python
from . import harness as _h  # noqa: F401
```

- [ ] **Step 2.5: 创建 7 个非占位子包的最小 harness（只有 __init__ + harness.py）**

`evaluation/memory/harness.py`（**D2 deferred 占位**：返回 expected keys as deferred）：

```python
"""memory dim harness——P14b 阶段实装 Langfuse trace 查询。

P14 阶段（骨架）：dispatcher hook + 真正 deferred 占位。
D2 边界：exp schema 已定义（recalled / types_any_of），但 observation 暂不可用——
返回 `(sections=[], deferred_keys=list(exp.keys()))`，让 dim_results[memory] 的
`deferred` 字段等于期望 key 数，用户能区分「schema 未定义」（frontend/e2e）vs
「schema 已定但 observation 未接入」（memory/retrieval/tool_selection/repair）。

P14b 阶段：扩 ObservedTurn.langfuse_trace + 接入 backend/app/observability/langfuse_flush.py
读 memory_recall_observed / memory_types_observed，比对 exp，sections 替换 deferred。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("memory")
def assert_memory(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。

    exp 形如：
      {"recalled": bool, "types_any_of": ["conversation", "session"]}

    返回：(sections=[], deferred_keys=每个 exp key)
    """
    _ = obs
    return {}, list(exp.keys())
```

`evaluation/retrieval/harness.py`（**D2 deferred 占位**：同上模式）：

```python
"""retrieval dim harness——P14b 阶段实装 Langfuse tool/observation 读 retrieval count。

D2 边界：与 memory 同——exp schema 已定义、observation 暂不可用 →
(`sections=[], deferred_keys=list(exp.keys())`)。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("retrieval")
def assert_retrieval(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。exp 形如 {"recalled": bool, "k_min": 1}。"""
    _ = obs
    return {}, list(exp.keys())
```

`evaluation/tool_selection/harness.py`（**D2 deferred 占位**：同上模式）：

```python
"""tool_selection dim harness——P14b 阶段读 Langfuse tool_call span list；

P14 阶段 deferred 占位（D2 边界）。P14b 实装：从 obs.langfuse_trace 抽取 tool name 列表比对 exp。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("tool_selection")
def assert_tool_selection(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。"""
    _ = obs
    return {}, list(exp.keys())
```

`evaluation/sql/harness.py`（D2 实装：真判定，sections 写值）：

```python
"""sql dim harness——P14 阶段即实装：复用 execution 子段 sql/rows_gt 判定。

D2 边界：本子包是「exp schema 已定义 + observation 可用」——返回真实 sections，
通过 dispatcher 写入到 `sql.<key>` section。在 dim_results[sql] 显示 pass/fail，
不计入 deferred。

注：legacy execution 段对相同字段写 `execution.<key>` section——两套并存但 prefix
不同（sql.* vs execution.*），互不重复。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("sql")
def assert_sql(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    deferred: list[str] = []
    # sql_nonempty
    if exp.get("sql_nonempty") is True:
        sections["sql_nonempty"] = (
            "pass" if bool(obs.sql and obs.sql.strip()) else "fail"
        )
    # rows_gt
    if exp.get("rows_gt") is not None:
        rc = obs.row_count
        sections["rows_gt"] = "pass" if rc is not None and rc > exp["rows_gt"] else "fail"
    # verdict
    if exp.get("verdict") is not None:
        sections["verdict"] = (
            "pass" if (
                (exp["verdict"] == "FAILED" and obs.error_code)
                or (exp["verdict"] == "EMPTY" and (obs.row_count == 0))
                or (exp["verdict"] == "SUCCESS" and (obs.row_count and obs.row_count > 0))
            ) else "fail"
        )
    return sections, deferred
```

`evaluation/repair/harness.py`（**D2 deferred 占位**：同上模式）：

```python
"""repair dim harness——P14b 阶段读 Langfuse 中 sql.repair span 比对 retry_count。

D2 边界：与 memory 同——exp schema 已定义、observation 暂不可用 →
`(sections=[], deferred_keys=list(exp.keys())`)。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("repair")
def assert_repair(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """P14 deferred 占位；P14b 实装。exp 形如 {"used": bool, "retries_max": 2, "succeeded_within_budget": bool}。"""
    _ = obs
    return {}, list(exp.keys())
```

`evaluation/requirement/harness.py`（**D2 实装**：4 类期望判定）：

```python
"""requirement dim harness——RequirementCard 字段级判定实装。

复用 evaluation/checker.py:53-71 的 legacy requirement 段逻辑，封装为子包函数。
section key 不带 prefix：status / min_missing_fields / time_range_equals / target_metrics。
dispatcher 调时自动加 `requirement.` prefix（与 legacy 段 section key 不冲突）。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("requirement")
def assert_requirement(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    deferred: list[str] = []
    if exp.get("status") is not None:
        sections["status"] = "pass" if obs.card_status == exp["status"] else "fail"
    if exp.get("min_missing_fields") is not None:
        got = obs.missing_fields_count
        sections["min_missing_fields"] = (
            "pass" if got is not None and got >= exp["min_missing_fields"] else "fail"
        )
    if exp.get("time_range_equals") is not None:
        sections["time_range_equals"] = (
            "pass" if obs.time_range == exp["time_range_equals"] else "fail"
        )
    if exp.get("target_metrics_contains"):
        want_any = exp["target_metrics_contains"]
        hit = any(any(w in m for m in obs.target_metrics) for w in want_any)
        sections["target_metrics"] = "pass" if hit else "fail"
    return sections, deferred
```

`evaluation/report/harness.py`（**实装**：3 类期望判定，KPI / Table / Chart）：

```python
"""report dim harness——ReportSpec 字段溯源判定。

复用 evaluation/checker.py:95-109 legacy report 段逻辑，但 focus 在
P10 三层 Validator 感兴趣的字段（KPI / Table 字段 / Chart type）。
"""
from __future__ import annotations

from evaluation.checker import ObservedTurn, register_dim


@register_dim("report")
def assert_report(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    deferred: list[str] = []
    if exp.get("table_present") is not None:
        sections["table_present"] = (
            "pass" if obs.table_present == exp["table_present"] else "fail"
        )
    if exp.get("chart_present") is not None:
        sections["chart_present"] = (
            "pass" if obs.chart_present == exp["chart_present"] else "fail"
        )
    if exp.get("rows_gt") is not None:
        tr = obs.table_rows
        sections["rows_gt"] = "pass" if tr is not None and tr > exp["rows_gt"] else "fail"
    return sections, deferred
```

`evaluation/{requirement,memory,retrieval,tool_selection,sql,repair,report}/__init__.py` 各自：

```python
from . import harness as _harness  # noqa: F401
```

- [ ] **Step 2.6: 创建 7 个子包的 test_harness.py（最小 test）**

`evaluation/requirement/tests/test_harness.py`：

```python
"""requirement 子包 dispatcher 单测。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import ObservedTurn, check_turn
from evaluation.requirement.harness import assert_requirement


def test_assert_requirement_status_complete_pass():
    obs = ObservedTurn(card_status="complete")
    sec, _ = assert_requirement(obs, {"status": "complete"})
    assert sec == {"status": "pass"}


def test_assert_requirement_status_missing_fail():
    obs = ObservedTurn(card_status="complete")
    sec, _ = assert_requirement(obs, {"status": "missing"})
    assert sec == {"status": "fail"}


def test_assert_requirement_min_missing_fields():
    obs = ObservedTurn(missing_fields_count=3)
    sec, _ = assert_requirement(obs, {"min_missing_fields": 1})
    assert sec["min_missing_fields"] == "pass"


def test_assert_requirement_target_metrics_contains_hit():
    obs = ObservedTurn(target_metrics=["销售额", "订单"])
    sec, _ = assert_requirement(obs, {"target_metrics_contains": ["销售额"]})
    assert sec["target_metrics"] == "pass"


def test_dispatch_through_check_turn_section_prefix():
    """走 dispatcher 时 section key 带 dim prefix；requirement 在 LEGACY_KEYS 内只走 Phase 1。"""
    obs = ObservedTurn(card_status="complete", target_metrics=["销售额"])
    sec, _ = check_turn(obs, {"requirement": {"status": "complete", "target_metrics_contains": ["销售额"]}})
    # Phase 1 唯一写入 requirement.* section；Phase 2 dispatcher 跳过 requirement
    # （LEGACY_KEYS 过滤，见 D1 边界）——所以 requirement.* 只出现一次
    requirement_section_keys = [k for k in sec if k.startswith("requirement.")]
    assert "requirement.status" in sec
    assert sec["requirement.status"] == "pass"
    # D1 边界：requirement 不会被 Phase 2 dispatcher 重写——仅一份 section
    assert requirement_section_keys.count("requirement.status") == 1
```

注：D1 边界——`requirement` 在 LEGACY_KEYS 内，Phase 2 dispatcher 跳过。test 5 验证 LEGACY_KEYS skip 逻辑，避免 Phase 1 legacy 写入与 Phase 2 dispatcher 产生重复 section key。

`evaluation/memory/tests/test_harness.py`（**D2 deferred** 测试）：

```python
"""memory 子包 dispatcher 注册测试（D2 deferred 占位；P14b 实装）。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.memory.harness import assert_memory


def test_memory_registered():
    assert DIM_REGISTRY.get("memory") is assert_memory


def test_memory_p14a_returns_deferred_keys():
    """D2 边界：placeholder 返回 (sections=[], deferred_keys=list(exp.keys()))。

    与 frontend/e2e 真 no-op 区分——memory 是「schema 已定、observation 暂不可用」，
    deferred 列表里每个 expected key 都出现，dim_results[memory]['deferred'] 会等于期望数。
    """
    obs = ObservedTurn()
    sec, deferred = assert_memory(obs, {"recalled": True, "types_any_of": ["conversation"]})
    assert sec == {}
    assert set(deferred) == {"recalled", "types_any_of"}


def test_dispatch_through_check_turn_defers_memory_keys():
    """通过 dispatcher 跑 memory exp → sections 不写值，deferred 加 prefix 包装 memory.*。"""
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"memory": {"recalled": True, "types_any_of": ["session"]}})
    # sections 不写 memory.*（placeholder sections=[]）
    assert not any(k.startswith("memory.") for k in sec.keys())
    # deferred 列表带 'memory.' prefix
    assert "memory.recalled" in deferred
    assert "memory.types_any_of" in deferred
```

`evaluation/retrieval/tests/test_harness.py`（**D2 deferred** 测试）：

```python
"""retrieval 子包 dispatcher 注册测试（D2 deferred 占位）。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.retrieval.harness import assert_retrieval


def test_retrieval_registered():
    assert DIM_REGISTRY.get("retrieval") is assert_retrieval


def test_retrieval_p14a_returns_deferred_keys():
    """D2 deferred：placeholder 返回 (sections=[], deferred_keys=list(exp.keys()))。"""
    obs = ObservedTurn()
    sec, deferred = assert_retrieval(obs, {"recalled": True, "k_min": 1})
    assert sec == {}
    assert set(deferred) == {"recalled", "k_min"}


def test_dispatch_through_check_turn_defers_retrieval_keys():
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"retrieval": {"recalled": True}})
    assert not any(k.startswith("retrieval.") for k in sec.keys())
    assert "retrieval.recalled" in deferred
```

`evaluation/tool_selection/tests/test_harness.py`（**D2 deferred** 测试）：

```python
"""tool_selection 子包 dispatcher 注册测试（D2 deferred 占位）。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.tool_selection.harness import assert_tool_selection


def test_tool_selection_registered():
    assert DIM_REGISTRY.get("tool_selection") is assert_tool_selection


def test_tool_selection_p14a_returns_deferred_keys():
    """D2 deferred：empty exp 返回空 deferred；非空 exp 返回 keys 列表。"""
    obs = ObservedTurn()
    # empty exp 边界（list({}) = []）
    sec, deferred = assert_tool_selection(obs, {})
    assert sec == {}
    assert deferred == []
    # 非空 exp → deferred = list(exp.keys())
    sec2, deferred2 = assert_tool_selection(obs, {"tool_chosen": "search_schema"})
    assert sec2 == {}
    assert deferred2 == ["tool_chosen"]


def test_dispatch_through_check_turn_defers_tool_selection_keys():
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"tool_selection": {"tool_chosen": "search_schema"}})
    assert not any(k.startswith("tool_selection.") for k in sec.keys())
    assert "tool_selection.tool_chosen" in deferred
```

`evaluation/sql/tests/test_harness.py`（**实装**测试）：

```python
"""sql 子包 dispatcher 实装测试。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import ObservedTurn
from evaluation.sql.harness import assert_sql


def test_sql_sql_nonempty_pass():
    obs = ObservedTurn(sql="SELECT 1")
    sec, _ = assert_sql(obs, {"sql_nonempty": True})
    assert sec["sql_nonempty"] == "pass"


def test_sql_sql_nonempty_fail():
    obs = ObservedTurn(sql="")
    sec, _ = assert_sql(obs, {"sql_nonempty": True})
    assert sec["sql_nonempty"] == "fail"


def test_sql_rows_gt_pass():
    obs = ObservedTurn(row_count=10)
    sec, _ = assert_sql(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "pass"


def test_sql_rows_gt_unknown():
    obs = ObservedTurn(row_count=None)
    sec, _ = assert_sql(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "fail"


def test_sql_verdict_success():
    obs = ObservedTurn(row_count=10, sql="SELECT 1")
    sec, _ = assert_sql(obs, {"verdict": "SUCCESS"})
    assert sec["verdict"] == "pass"


def test_sql_verdict_empty():
    obs = ObservedTurn(row_count=0, sql="SELECT 1")
    sec, _ = assert_sql(obs, {"verdict": "EMPTY"})
    assert sec["verdict"] == "pass"


def test_sql_verdict_failed():
    obs = ObservedTurn(sql="SELECT 1", error_code="SQL_SYNTAX_ERROR")
    sec, _ = assert_sql(obs, {"verdict": "FAILED"})
    assert sec["verdict"] == "pass"
```

`evaluation/repair/tests/test_harness.py`（**D2 deferred** 测试）：

```python
"""repair 子包 dispatcher 注册测试（D2 deferred 占位）。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY
from evaluation.repair.harness import assert_repair


def test_repair_registered():
    assert DIM_REGISTRY.get("repair") is assert_repair


def test_repair_p14a_returns_deferred_keys():
    """D2 deferred：(sections=[], deferred_keys=list(exp.keys()))。"""
    obs = ObservedTurn()
    sec, deferred = assert_repair(obs, {"used": True, "retries_max": 2, "succeeded_within_budget": True})
    assert sec == {}
    assert set(deferred) == {"used", "retries_max", "succeeded_within_budget"}


def test_dispatch_through_check_turn_defers_repair_keys():
    obs = ObservedTurn()
    sec, deferred = check_turn(obs, {"repair": {"used": True}})
    assert not any(k.startswith("repair.") for k in sec.keys())
    assert "repair.used" in deferred
```

`evaluation/report/tests/test_harness.py`（**实装**测试）：

```python
"""report 子包 dispatcher 实装测试。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import ObservedTurn
from evaluation.report.harness import assert_report


def test_report_table_present_pass():
    obs = ObservedTurn(table_present=True)
    sec, _ = assert_report(obs, {"table_present": True})
    assert sec["table_present"] == "pass"


def test_report_table_present_fail():
    obs = ObservedTurn(table_present=False)
    sec, _ = assert_report(obs, {"table_present": True})
    assert sec["table_present"] == "fail"


def test_report_chart_present_pass():
    obs = ObservedTurn(chart_present=True)
    sec, _ = assert_report(obs, {"chart_present": True})
    assert sec["chart_present"] == "pass"


def test_report_rows_gt_pass():
    obs = ObservedTurn(table_rows=10)
    sec, _ = assert_report(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "pass"


def test_report_rows_gt_unknown():
    obs = ObservedTurn(table_rows=None)
    sec, _ = assert_report(obs, {"rows_gt": 5})
    assert sec["rows_gt"] == "fail"
```

- [ ] **Step 2.7: 跑子包测试**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_subpackage_layout.py evaluation/requirement/tests/ evaluation/memory/tests/ evaluation/retrieval/tests/ evaluation/tool_selection/tests/ evaluation/sql/tests/ evaluation/repair/tests/ evaluation/report/tests/ -v
```

预期：所有 PASS

- [ ] **Step 2.8: 跑 dispatcher 测试**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_dispatcher.py -v
```

预期：所有 PASS（9 子包已注册，dispatch 跑通）

- [ ] **Step 2.9: 全量 evaluation suite（**确认零回归**）**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/ -v
```

预期：所有 PASS（含原有 5 个文件 + 7 个新子包 test_*.py + dispatcher / layout）

- [ ] **Step 2.10: Commit 9 子包骨架**

```bash
git add evaluation/checker.py evaluation/{requirement,memory,retrieval,tool_selection,sql,repair,report,frontend,e2e}/
git commit -m "feat(evaluation): 9 子包 dispatcher 骨架（requirement + report 实装，5 占位，frontend/e2e no-op）"
```

---

### Task 3: `evaluation/checker.py` 加 `build_dim_results` 纯函数 + runner 使用

**Files:**
- Modify: `evaluation/checker.py`（加 `build_dim_results` 纯函数）
- Modify: `evaluation/runner.py:226-232`（result dict 加 `dim_results`，通过 `build_dim_results` 计算）

- [ ] **Step 3.1: 写 `build_dim_results` 行为测试（先红）**

新建 `evaluation/tests/test_checker_dim_results.py`：

```python
"""evaluation.checker.build_dim_results 纯函数行为测试。

设计评审要求：不依赖 runner.run_case，直接 unit test 一个纯函数，
避免「源码文本 contains XXX」式的伪测试。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.evaluation

from evaluation.checker import build_dim_results


def test_build_dim_results_pass_section_counts():
    """纯函数：section 写 pass → 对应 dim 的 pass 计数增加。"""
    sections = {
        "sql.sql_nonempty": "pass",
        "sql.rows_gt": "pass",
    }
    out = build_dim_results(sections, deferred=[], dims=["sql"])
    assert out["sql"] == {"pass": 2, "fail": 0, "deferred": 0}


def test_build_dim_results_fail_section_startswith_fail():
    """fail 类 section 形式包含 'fail' 前缀（e.g. 'fail(derived=EMPTY)'）也算 fail。"""
    sections = {"sql.rows_gt": "fail(derived=EMPTY)"}
    out = build_dim_results(sections, deferred=[], dims=["sql"])
    assert out["sql"]["fail"] == 1
    assert out["sql"]["pass"] == 0


def test_build_dim_results_deferred_keys_counted():
    """deferred list 包含 dim 前缀的 key → dim 的 deferred 计数增加。"""
    sections = {"report.table_present": "pass"}
    deferred = ["memory.recalled", "memory.types_any_of"]
    out = build_dim_results(sections, deferred, dims=["report", "memory"])
    assert out["report"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["memory"] == {"pass": 0, "fail": 0, "deferred": 2}


def test_build_dim_results_dim_unknown_returns_zero():
    """未出现的 dim 返回 0/0/0 默认值。"""
    out = build_dim_results({}, [], dims=["sql", "memory"])
    assert out["sql"] == {"pass": 0, "fail": 0, "deferred": 0}
    assert out["memory"] == {"pass": 0, "fail": 0, "deferred": 0}


def test_build_dim_results_multiple_dims_separated():
    """多 dim sections 各自归属（不串）——用 `dim.` 前缀边界而非 `dim` contains。"""
    sections = {
        "sql.sql_nonempty": "pass",
        "sqltable.rows_gt": "pass",  # 不应被 sql.* 误归
    }
    out = build_dim_results(sections, [], dims=["sql"])
    # 'sqltable.' 与 'sql.' 是不同 prefix（前缀边界 = '.'）
    assert out["sql"] == {"pass": 1, "fail": 0, "deferred": 0}


def test_build_dim_results_calls_with_legacy_dims():
    """runner 调用 build_dim_results 时 dims 包含 legacy 4 段+registry 9 段共 11 维度；纯函数不关心。"""
    sections = {
        "requirement.status": "pass",
        "execution.verdict": "pass",
        "report.table_present": "pass",
        "behavior.clarification": "pass",
    }
    out = build_dim_results(sections, [], dims=["requirement", "execution", "report", "behavior"])
    assert out["requirement"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["execution"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["report"] == {"pass": 1, "fail": 0, "deferred": 0}
    assert out["behavior"] == {"pass": 1, "fail": 0, "deferred": 0}
```

- [ ] **Step 3.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_checker_dim_results.py -v
```

预期：FAIL（`build_dim_results` 未定义）

- [ ] **Step 3.3: 在 `evaluation/checker.py` 加 `build_dim_results` 纯函数**

修改 `evaluation/checker.py`，在 `summarize` 函数前插入：

```python
def build_dim_results(
    sections: dict[str, str],
    deferred: list[str],
    dims: "list[str] | tuple[str, ...] | set[str]",
) -> dict[str, dict[str, int]]:
    """聚合各 dim 的 pass/fail/deferred 数量。

    纯函数，**不**依赖 runner / DIM_REGISTRY / 测试依赖。
    输入：
      - sections: 已合并的 section dict（legacy + dispatcher 都贡献 key）
      - deferred: 已合并的 deferred key 列表
      - dims: 要聚合的 dim 列表（runner 调时 = registry 9 + legacy 4 = 13）
    输出：
      {dim: {"pass": int, "fail": int, "deferred": int}}

    dim 归属规则：`k.startswith(f"{dim}.")`（前缀 + dot 是边界），
    避免 `dim` contains 误把 `sqltable.foo` 归到 `sql`。
    """
    out: dict[str, dict[str, int]] = {}
    for dim in dims:
        prefix = f"{dim}."
        keys_pass = sum(1 for k, v in sections.items() if k.startswith(prefix) and v == "pass")
        keys_fail = sum(
            1 for k, v in sections.items() if k.startswith(prefix) and v.startswith("fail")
        )
        keys_deferred = sum(1 for k in deferred if k.startswith(prefix))
        out[dim] = {"pass": keys_pass, "fail": keys_fail, "deferred": keys_deferred}
    return out
```

- [ ] **Step 3.4: 跑测试确认绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_checker_dim_results.py -v
```

预期：6/6 PASS

- [ ] **Step 3.5: 改 `evaluation/runner.py` 使用 `build_dim_results`**

修改 `evaluation/runner.py:226-232`：

```python
        latency_ms = (time.monotonic() - t0) * 1000.0
        status = "fail" if any(v.startswith("fail") for v in sections_all.values()) else "pass"

        # P14: dim_results = LEGACY_KEYS ∪ DIM_REGISTRY
        from evaluation.checker import DIM_REGISTRY, build_dim_results
        all_dims = list(DIM_REGISTRY.keys()) + ["requirement", "execution", "report", "behavior"]
        # set(...) 去重（requirement / report 在两组都有）
        seen: set[str] = set()
        unique_dims: list[str] = []
        for d in all_dims:
            if d not in seen:
                seen.add(d)
                unique_dims.append(d)
        dim_results = build_dim_results(
            sections_all,
            sorted(set(deferred_all)),
            unique_dims,
        )

        return {
            "case_id": case.id, "category": case.category, "status": status,
            "sections": sections_all,
            "deferred": sorted(set(deferred_all)),
            "dim_results": dim_results,  # P14 新增字段
            "sql_executed": sql_executed,
            "latency_ms": round(latency_ms, 1),
        }
```

- [ ] **Step 3.6: 全量 evaluation suite 验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/ -v
```

预期：所有 PASS（既有 + 12 新 test_*.py + 6 build_dim_results 单测）

- [ ] **Step 3.7: Commit build_dim_results + runner 升级**

```bash
git add evaluation/checker.py evaluation/runner.py evaluation/tests/test_checker_dim_results.py
git commit -m "feat(evaluation): checker.build_dim_results 纯函数 + runner.run_case output 加 dim_results"
```

---

### Task 4: 全量回归 + P14 plan 收尾

**Files:**
- Modify: `docs/plans/2026-09-01-p14-evaluation-skeleton.md`（顶部加落地记录段 + 状态维持「进行中」直到 review PASS）
- Modify: `docs/plans/README.md`（无需改动——已 admin 时登记）

- [ ] **Step 4.1: 后端全量回归**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
```

预期：**990+N passed / 1 skipped / 5 warnings**（N = 新增子包 test 数）

- [ ] **Step 4.2: 前端零回归**

```bash
cd frontend && npm run lint
cd frontend && npm run test:run
```

预期：与 P13 master 一致

- [ ] **Step 4.3: 跑一遍 dispatch smoke（D1 / D2 / build_dim_results 三向验证）**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -c "
from evaluation.checker import check_turn, ObservedTurn, DIM_REGISTRY, build_dim_results

# 1. DIM_REGISTRY 9 个 dim 全部注册（D1 边界：含 requirement/report 兼容注册位）
expected = {'requirement', 'memory', 'retrieval', 'tool_selection', 'sql', 'repair', 'report', 'frontend', 'e2e'}
assert expected == set(DIM_REGISTRY.keys()), f'mismatch: {expected ^ set(DIM_REGISTRY.keys())}'

# 2. Phase 1 legacy 4 段向后兼容
obs = ObservedTurn(card_status='complete', target_metrics=['销售额'], time_range='2024年', sql='SELECT 1', row_count=10)
sec, _ = check_turn(obs, {
    'requirement': {'status': 'complete', 'target_metrics_contains': ['销售额'], 'time_range_equals': '2024年'},
    'execution': {'verdict': 'SUCCESS', 'sql_nonempty': True},
})
assert sec['requirement.status'] == 'pass'
assert sec['requirement.target_metrics'] == 'pass'
assert sec['execution.verdict'] == 'pass'
assert sec['execution.sql_nonempty'] == 'pass'
# D1 边界：requirement.section 只出现一份（Phase 2 dispatcher 跳过）
assert len([k for k in sec if k.startswith('requirement.')]) == 1

# 3. Phase 2 active dispatch: sql dim（不在 LEGACY_KEYS） → sections 写 sql.* prefix
sec2, _ = check_turn(obs, {'sql': {'sql_nonempty': True, 'rows_gt': 5}})
assert 'sql.sql_nonempty' in sec2 and sec2['sql.sql_nonempty'] == 'pass'

# 4. Phase 2 deferred (D2): memory exp → sections 空 + deferred 包含 memory.*
sec3, deferred3 = check_turn(obs, {'memory': {'recalled': True}, 'retrieval': {'k_min': 1}})
assert not any(k.startswith('memory.') for k in sec3.keys())
assert 'memory.recalled' in deferred3
assert 'retrieval.k_min' in deferred3

# 5. 真 no-op (D2): frontend / e2e exp → sections 空 + deferred 也空
sec4, deferred4 = check_turn(obs, {'frontend': {'any_key': True}, 'e2e': {'e2e_x': True}})
assert not any(k.startswith('frontend.') for k in sec4.keys())
assert not any(k.startswith('frontend.') for k in deferred4)
assert not any(k.startswith('e2e.') for k in sec4.keys())
assert not any(k.startswith('e2e.') for k in deferred4)

# 6. build_dim_results 纯函数聚合（验证 dim_results 字段算法）
sections = {'sql.sql_nonempty': 'pass', 'sql.rows_gt': 'pass',
            'requirement.status': 'pass', 'report.table_present': 'pass'}
deferred = ['memory.recalled', 'memory.types_any_of']
dim_results = build_dim_results(sections, deferred,
    ['sql', 'memory', 'requirement', 'report', 'frontend'])
assert dim_results['sql'] == {'pass': 2, 'fail': 0, 'deferred': 0}
assert dim_results['memory'] == {'pass': 0, 'fail': 0, 'deferred': 2}
assert dim_results['requirement'] == {'pass': 1, 'fail': 0, 'deferred': 0}

print('OK: D1 legacy skip + D2 deferred/noop + dispatcher 7 active dim + build_dim_results 全 PASS')
"
```

预期：`OK: D1 legacy skip + D2 deferred/noop + ...`

- [ ] **Step 4.4: 9 子包目录在场**

```bash
ls evaluation/requirement/harness.py evaluation/memory/harness.py evaluation/retrieval/harness.py evaluation/tool_selection/harness.py evaluation/sql/harness.py evaluation/repair/harness.py evaluation/report/harness.py evaluation/frontend/harness.py evaluation/e2e/harness.py
```

预期：9 个文件全列

- [ ] **Step 4.5: docs/CONTRIBUTING（可选，更新 evaluation/README.md）**

新建 `evaluation/README.md`（P14 阶段可有可无——后续 P14b 时一起出）：

```markdown
# ReportAgent Evaluation Harness

## 目录结构（P14 起为伞形 plan §十四终态）

- `baseline_cases.json` — 20 例行为期望数据
- `checker.py` — dispatcher 入口（legacy 4 段 + 9 子包）
- `runner.py` — 真实 API 驱动 baseline（manual gate；env-gated）
- `schema.py` — Pydantic 模型（schema 冻结）
- `loader.py` — case 加载
- `<dim>/` — 9 子包，每个各持有 `harness.py` + `tests/`
  - `requirement / memory / retrieval / tool_selection / sql / repair / report` — 实装
  - `frontend / e2e` — 占位

## 跑法

```bash
# 单测
cd backend && pytest evaluation/ -v

# 真实 API（manual gate，需要 backend 在跑）
cd backend && REPORTAGENT_E2E=1 python -m evaluation.runner --base-url http://localhost:8100 --out eval-result.json --md eval-result.md
```

## 添加新 dim 期望

在 expectation dict 里新增 dim key（与 Pydantic schema 解耦）：

```json
{
  "expectations": [
    {
      "requirement": {...},         // legacy Phase 1
      "execution": {...},
      "report": {...},
      "behavior": {...},
      "memory": {"recalled": true}, // Phase 2 dispatcher
      "retrieval": {"recalled": true}
    }
  ]
}
```

## P14b / P14c 接力

- P14b：扩 ObservedTurn.langfuse_trace；memory / retrieval / tool_selection / repair 子包读 Langfuse trace 实装；baseline/optimized 对比机制
- P14c：regression detection 自动化（compare dim_results across runs）
```

- [ ] **Step 4.6: Commit 收尾**

```bash
git add docs/plans/2026-09-01-p14-evaluation-skeleton.md evaluation/README.md
git commit -m "docs(p14): evaluation/README.md（9 子包用法 + P14b/c 接力点）+ plan 阶段收尾"
```

---

## Self-Review

1. **Spec coverage**:
   - 9 子包目录：Task 2 ✓（Step 2.4-2.5 创建 9 个 `__init__.py` + `harness.py`）
   - dispatcher pattern：Task 1 ✓（DIM_REGISTRY + register_dim 装饰器 + check_turn Phase 2 dispatcher）
   - D1 边界（9 注册 + 7 active dispatch）：Task 1 ✓（`LEGACY_KEYS = {"requirement", "execution", "report", "behavior"}` 跳过 requirement/report Phase 2；execution/behavior 永不在 registry）
   - D2 边界（deferred vs noop）：Task 2 ✓
     - placeholder 子包（memory / retrieval / tool_selection / repair）→ `({}, list(exp.keys()))`，dim_results 计 deferred
     - 真 no-op（frontend / e2e）→ `({}, [])`，dim_results 全 0 是预期
   - build_dim_results 纯函数：Task 3 ✓（6 个行为单测，无 `inspect.getsource()` 伪测试）
   - runner.run_case output 加 dim_results：Task 3 Step 3.5 ✓（通过 build_dim_results 调用）
   - 各 dim 1 example case：Task 2 ✓（每个子包 `tests/test_harness.py` 含 3-7 个 fixture 测试）
   - 不增加 baseline categories：Step 0 + Step 2 ✓（baseline_cases.json 不动）
   - frontend / e2e 真 no-op：Step 2.4 ✓
   - 自动 Evaluation：Task 3 ✓（runner output dim_results；summarize 不变）

2. **Placeholder scan**:
   - "P14b 填" 在 memory / retrieval / tool_selection / repair 4 处出现 —— 是 deferred 占位说明（plan 边界明示，P14b 接力填 Langfuse trace 查询逻辑），不是 TBD 占位任务
   - 无 TODO / fill in details

3. **Type consistency**:
   - `DIM_REGISTRY`：`dict[str, Callable[["ObservedTurn", dict], tuple[dict[str, str], list[str]]]]` —— 9 子包函数签名一致
   - `register_dim(name: str)` 装饰器签名稳定 + 幂等（`if name not in DIM_REGISTRY`）
   - `LEGACY_KEYS = {"requirement", "execution", "report", "behavior"}` —— Task 1 Step 1.4 一处定义，Type consistency 中提及时使用同一名字（已修正）
   - `build_dim_results(sections, deferred, dims)` —— checker.py 一处定义；Test 6 例函数签名引用一致；runner.run_case 调时第三参用 list（task 3 step 3.5）
   - `run_case` result dict 加 `dim_results: dict[str, dict[str, int]]` —— 通过 `build_dim_results` 填充；Task 3 Step 3.5 单点写入
   - `check_turn` 仍返回 `tuple[dict[str, str], list[str]]` —— Phase 1 + Phase 2 合并 sections，签名不变

4. **Compatibility**:
   - `baseline_cases.json` 20 例：schema 不动 → existing `test_checker` / `test_schema` / `test_dataset` / `test_report_render` / `test_runner_integration` 5 文件预期 PASS
   - `evaluation/schema.py`：不动 → 兼容
   - `evaluation/checker.py` legacy 4 段：保留 → P0-P12 期望 section key 一致
   - `evaluation/runner.py`：仅加 `dim_results` 字段，老用法不变 → e2e/test_full_flow.py 的引用若有用到 result dict 只读新增字段不破坏

5. **评审响应**（2026-09-01 用户 review 反馈）：
   - D1 ⚠️ → ✅ Section 2 / Task 1 / Self-Review 1.4 / Type consistency 全部明确 LEGACY_KEYS 边界
   - D2 ❌ → ✅ Step 2.4 / 2.5 / 2.6 全部明确 placeholder 返回 `({}, list(exp.keys()))`；frontend/e2e 维持 `({}, [])`
   - Task 3 测试写法 🟡 → ✅ Task 3 Step 3.1 重写为 `build_dim_results` 纯函数行为测试（6 例），无源码文本检测
   - typo `evaluation/evaluation/*` → ✅ 已修

6. **Out-of-scope 明示**（Explicitly NOT doing）：见 plan §「Explicitly NOT doing」10 条
