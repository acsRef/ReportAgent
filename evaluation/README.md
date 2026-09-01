# ReportAgent Evaluation Harness

P14 起按伞形 plan §十四目录终态重组。

## 目录结构（伞形 §十四终态：9 子包 + core）

```text
evaluation/
├── __init__.py               # pkg 标记
├── baseline_cases.json       # 20 例行为期望（P0 冻结，现状 13 categories）
├── checker.py                # ObservedTurn / check_turn / build_dim_results / DIM_REGISTRY / LEGACY_KEYS
├── loader.py                 # case 加载
├── runner.py                 # 真实 API 驱动 baseline（manual gate；env-gated）
├── schema.py                 # Pydantic 模型（schema 冻结）
├── tests/                    # 既有 P0-P13 单测 + P14 dispatcher / dim_results
├── requirement/              # 实装：RequirementCard 字段级判定
├── memory/                   # D2 deferred（Langfuse trace 查询 P14b 实装）
├── retrieval/                # D2 deferred（同上）
├── tool_selection/           # D2 deferred（同上）
├── sql/                      # 实装：复用 execution 字段判定
├── repair/                   # D2 deferred（sql.repair span P14b 实装）
├── report/                   # 实装：KPI/Table/Chart 字段溯源
├── frontend/                 # 真 no-op（P11 契约冻结；本 plan 不重测）
└── e2e/                      # 真 no-op（P12 Playwright；本 plan 不重测）
```

## dispatcher 协议（D1 + D2 边界）

**DIM_REGISTRY 注册 9 dim**：`requirement / memory / retrieval / tool_selection / sql / repair / report / frontend / e2e`。

**Phase 1 legacy 唯一负责**：`requirement / execution / report / behavior` 4 段（向后兼容 P0-P12 行为）。

**Phase 2 active dispatch 7 dim**：`memory / retrieval / tool_selection / sql / repair / frontend / e2e`（`requirement` / `report` 注册但被 `LEGACY_KEYS` frozenset 跳过——避免与 Phase 1 legacy section key 重复）。

## 子包 harness 函数签名

```python
@register_dim("<dim>")
def assert_<dim>(obs: ObservedTurn, exp: dict) -> tuple[dict[str, str], list[str]]:
    """返回 (sections, deferred_keys)。

    D2 边界——分两类：
    - 实装（requirement / report / sql）：返回真实 sections，dispatcher 写 `<dim>.<key>` 到 result
    - D2 deferred（memory / retrieval / tool_selection / repair）：
        返回 (sections=[], deferred_keys=list(exp.keys()))——让 dim_results 计 deferred
    - 真 no-op（frontend / e2e）：返回 (sections=[], deferred_keys=[])——无 expectation schema
    """
```

## 跑法

### 单测

```bash
# 从 repo root 跑（evaluation 不在 pytest.ini testpaths 内，需显式路径）
D:/miniConda/envs/agent/python.exe -m pytest evaluation/ -v
```

预期：**97+ passed**（既有 P0-P13 + P14 dispatcher 6 + dim_results 6 + layout 16 + 9 subpackage harness 29）。

### 真实 API（manual gate，需要 backend 在跑）

```bash
# reference —— P12 已 env-gated；需要 PG + backend :8100 + LLM key
D:/miniConda/envs/agent/python.exe -m evaluation.runner \
    --base-url http://127.0.0.1:8100 \
    --out  evaluation/results/baseline-2026-08-25.json \
    --md   evaluation/results/baseline-2026-08-25.md
```

`runner.run_case()` output 现在含 `dim_results` 字段（11 dim 槽位：9 registry + 4 legacy - 2 重叠 = 11）：

```python
{
    "case_id": "explicit-region-sales-ranking",
    "status": "pass",
    "sections": {"requirement.status": "pass", ...},
    "deferred": ["memory.recalled"],  # 出自 Phase 2 dispatcher（带 dim prefix）
    "dim_results": {
        "requirement": {"pass": 1, "fail": 0, "deferred": 0},
        "memory":      {"pass": 0, "fail": 0, "deferred": 1},
        "retrieval":   {"pass": 0, "fail": 0, "deferred": 0},
        ...
        "frontend":    {"pass": 0, "fail": 0, "deferred": 0},  # 真 no-op 形态
    },
    "latency_ms": 1234.5,
}
```

## 添加新 dim 期望

在 expectation dict 里加新 dim key，**不修改 Pydantic schema**（`evaluation/schema.py` 冻结）：

```json
{
  "expectations": [
    {
      "requirement": {"status": "complete"},          // Phase 1 legacy 唯一负责
      "execution":   {"verdict": "SUCCESS"},
      "report":      {"table_present": true},
      "behavior":    {"clarification": false},
      "memory":      {"recalled": true, "types_any_of": ["conversation"]},  // Phase 2 dispatcher
      "retrieval":   {"recalled": true, "k_min": 1},
      "sql":         {"sql_nonempty": true, "rows_gt": 0},
      "tool_selection": {"tool_chosen": "search_schema"}
    }
  ]
}
```

`dim_results[X]` 由 `build_dim_results` 纯函数从 sections + deferred 自动聚合。

## P14b / P14c 接力

- **P14b**：扩 `ObservedTurn.langfuse_trace: list[dict]`；memory / retrieval / tool_selection / repair 子包改函数体：从 `obs.langfuse_trace` 抽 memory_recall_observed / retrieval_count / tool_call_list / repair_retry_count，比对 exp，sections 替换 deferred；新增 baseline/optimized 对比机制（runner output 多写一份 baseline 快照，对比维度：通过率 / 退化率 / degraded_dim 列表）
- **P14c**：regression detection 自动化——多次 run 收集 `dim_results`，threshold-based 报警（`fail_count > 0` for any dim across N consecutive runs 即报警）；CI 集成（playwright contract E2E 后接入）

## 关键文件指针

- `evaluation/checker.py:18` `DIM_REGISTRY` 模块级 dict
- `evaluation/checker.py:25` `LEGACY_KEYS = frozenset({...})`
- `evaluation/checker.py:31` `register_dim(name)` 装饰器
- `evaluation/checker.py:160` `build_dim_results(sections, deferred, dims)` 纯函数
- `evaluation/runner.py:226` `run_case` 用 `build_dim_results` 聚合 `dim_results`
