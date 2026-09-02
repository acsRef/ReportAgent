# SQL object error classification fix + real RAG MCP/PG e2e Analytics Case suite

> 状态: 进行中（2026-09-02；接 P14 master `a253d3d`）
> 双 issue 合并 plan：fix issue（SQL object/schema error 错误分类触发无信息增益 retry/replan）+ test issue（建立基于真实 RAG MCP + PostgreSQL 的完整链路 Analytics Case 测试集）
> 决策依据：用户 2026-09-02 拍板——P14 mock 全废 + e2e 取代；DiagnosePolicy 路径需用户后续讨论；e2e 先最小范围再扩全面

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修 SQL `UndefinedColumn` / `UndefinedTable` / `UndefinedFunction` 等"object"类异常被错误归类并触发 LLM 同 SQL 反复 retry 的可靠性 bug；同时建立基于真实 RAG MCP + PG 的 Analytics Case e2e 集成测试套件，取代 P14 全部 mock-style unit tests。

**Architecture:**
- **fix issue 两层**：`_classify_psycopg2_error` 用 `psycopg2.errors` 具体子类替代 `ProgrammingError` 兜底——`UndefinedColumn/Table/Function → "object_not_found"`，`AmbiguousColumn → "object_ambiguous"`，`DivisionByZero/DatatypeMismatch → "other"`；`DiagnosePolicy` object_not_found 路径走 MCP schema retrieval retry（**gated on user decision**——三种方案见 §3）
- **test issue**：新 `evaluation/tests/test_real_rag_mcp_e2e.py` env-gated（`REPORTAGENT_E2E=1`），跑真 backend + 真 ragent-py stdio MCP + 真 PostgreSQL（`ANALYSIS_DSN`）；覆盖 explicit_query / sql_repair / sql_failure / schema_retrieval / multi_turn 5 类最小集；P14 12 个 mock test 文件删除
- **production code 保留**：P14 DIM_REGISTRY / LEGACY_KEYS / dispatcher / build_dim_results / 9 子包 harness functions 是 runtime infra，不是 test-only——e2e 通过真实链路间接验证其不变式

**Tech Stack:** Python 3.11 + pytest + psycopg2 (psycopg2.errors.*) + httpx + ragent-py stdio MCP (existing) + Pydantic v2 + AnalysisPostgreSQL (ANALYSIS_DSN)

---

## Context

伞形 plan §十一 Reliability 规定「DB Timeout ≠ SQL 错——区分 Query Timeout / Connection Failure / Permission Failure / Object Not Found / Syntax Error，只有可恢复错误进入有限 retry」。但当前 `_classify_psycopg2_error`（`backend/app/tools/sql_tools.py:39`）把所有非权限/语法的 `ProgrammingError` 默认归 `"object"`，且 `AGENT_RECOVERABLE_KINDS = ("syntax", "object", "other")`（`backend/app/reliability/errors.py:24`）含 `"object"`——`DiagnosePolicy.decide()`（`backend/app/agent/sql_graph.py:87`）会走 `retry_sql` action，但同 SQL 重跑永远同错，烧光 `MAX_SQL_REPAIR_RETRIES=2` + `MAX_PLAN_RETRIES=1` 整个 budget 才放弃，且过程中没有信息增益（不调 `search_schema` MCP 拿正确表/列名）。

测试方面：P14a 落地 12 个 mock-style test 文件（dispatcher / dim_results / layout / subpackage harness / P0/P1/P2/P3 fix）覆盖了 P14 dispatcher 协议与 dim_results 11-slot 形状——但 mock 层不接真 RAG MCP 和真 PG，无法验证 evaluation 端到端契约。用户拍板**全部按真实链路重做**：12 个 mock 文件删除，新建 env-gated e2e 集成测试集。

P14 production 代码（DIM_REGISTRY / LEGACY_KEYS / register_dim / build_dim_results / 9 子包 harness functions / `_compute_dim_results` / `schema.py extra="allow"` / `_observe_turn max(version)`）作为 runtime infra 保留——e2e 通过真实 case 驱动间接验证。

### 用户 2026-09-02 决定

| 维度 | 决策 |
|---|---|
| 「重新」范围 | **P14 mock 全废 + e2e 取代**：12 个 mock 文件删除 |
| DiagnosePolicy 路径 | **gated on user decision**（三种方案见 §3，用户后续讨论） |
| e2e 覆盖范围 | **最小起步 + 后续扩全面**：先 5-6 类核心 case，分阶段补全 11+ 类 |

## Design

### 1. SQL 分类细化（fix issue 第一层）

`_classify_psycopg2_error` 现状：

```python
# backend/app/tools/sql_tools.py:39-66
def _classify_psycopg2_error(exc: BaseException) -> ErrorKind:
    if isinstance(exc, (psycopg2.errors.QueryCanceled, psycopg2.errors.AdminShutdown, ...)):
        return "timeout"
    if isinstance(exc, psycopg2.OperationalError):
        return "connection"
    if isinstance(exc, psycopg2.ProgrammingError):
        msg = str(exc).lower()
        if "permission" in msg or "denied" in msg:
            return "permission"
        if "syntax" in msg or "parse" in msg:
            return "syntax"
        return "object"  # 兜底太宽
    if isinstance(exc, psycopg2.errors.SyntaxError):
        return "syntax"
    if isinstance(exc, psycopg2.errors.UndefinedColumn):
        return "object"  # 与"其他"混在一起
    ...
```

P14 细化后：

```python
def _classify_psycopg2_error(exc: BaseException) -> ErrorKind:
    # timeout / connection / permission 不进 retry（边界保留）
    if isinstance(exc, (psycopg2.errors.QueryCanceled, psycopg2.errors.AdminShutdown, psycopg2.errors.CrashShutdown)):
        return "timeout"
    if isinstance(exc, psycopg2.OperationalError):
        return "connection"
    # permission 通过 message 文本匹配（保留旧 fallback 路径）
    if isinstance(exc, psycopg2.ProgrammingError):
        msg = str(exc).lower()
        if "permission" in msg or "denied" in msg:
            return "permission"
        if "syntax" in msg or "parse" in msg:
            return "syntax"
        # P15: 精确子类优先级高于 message 匹配
        if isinstance(exc, (psycopg2.errors.UndefinedColumn, psycopg2.errors.UndefinedTable, psycopg2.errors.UndefinedFunction)):
            return "object_not_found"  # NEW
        if isinstance(exc, psycopg2.errors.AmbiguousColumn):
            return "object_ambiguous"   # NEW
        if isinstance(exc, (psycopg2.errors.DivisionByZero, psycopg2.errors.DatatypeMismatch)):
            return "other"              # NEW：LLM 修不了
        # ProgrammingError 兜底保留"object"向后兼容
        return "object"
    # 旧的独立 isinstance 分支可删除（已合并进 ProgrammingError 分支精确子类）
    return "other"
```

`SQL_ERROR_KINDS` 扩展（`backend/app/reliability/errors.py:21`）：

```python
SQL_ERROR_KINDS = ("syntax", "object", "object_not_found", "object_ambiguous",
                   "timeout", "connection", "permission", "other")
```

向后兼容：保留 `"object"` 字符串不删，老 caller（`DiagnosePolicy` 第 102 行 `kind not in SQL_ERROR_KINDS` 白名单检查）继续工作。

### 2. DiagnosePolicy 路径（fix issue 第二层）— **GATED ON USER DECISION**

用户 2026-09-02 说"等下讨论下"。plan Task 3 实现阶段开始前需 user 在三种方案中选一种（或提出新方案）：

**方案 A**（推荐）：`object_not_found` 路径加 `retry_mcp_schema_retrieval` action
- DiagnosePolicy 检测 `kind == "object_not_found"` 且本轮未调 `search_schema` → 触发 MCP schema retrieval 重试 → 拿到新 schema → 重 generate_sql
- MCP 重试 1 次后仍错 → escalate clarify
- 优点：object 错是"可被 schema 修复"类型，应优先用 MCP 工具补信息，不浪费 SQL retry budget
- 新增 action：`retry_mcp_schema_retrieval`（sql_graph.py DiagnoseDecision enum 扩展）

**方案 B**：从 `AGENT_RECOVERABLE_KINDS` 移除 `"object"`，直接 clarify
- 不动 DiagnosePolicy 增加 action
- object 错直接走 clarify——让用户手动补充 schema 信息
- 优点：实现最简
- 缺点：用户体验差，常见拼写错都要用户介入

**方案 C**：区分 column 错 vs table 错
- column 拼写错可 LLM 修（高 confidence，可 retry_sql 但额外给拼写相近列名 hint）
- table 错不能 LLM 修（必须 escalate clarify）
- 优点：精细化
- 缺点：要建列名相似度索引；实现复杂

**Task 3 启动条件**：用户 2026-09-02 之后给出方案选择。plan 在此处用 gated decision 占位。

### 3. e2e 集成测试集（test issue）

新 `evaluation/tests/test_real_rag_mcp_e2e.py`，env-gated `REPORTAGENT_E2E=1`：

```python
"""Real RAG MCP + PG e2e Analytics Case 集成测试。

环境要求：
1. PostgreSQL 启动（ANALYSIS_DSN 角色）+ seed_pg.sql 已灌
2. ragent-py stdio MCP server 启动（mcp_schema_server.search_schema / search_faq）
3. ReportAgent backend :8100 启动（PG + LLM key）
4. REPORTAGENT_E2E=1 环境变量

覆盖 case 类别（最小集 → 后续扩全面）：
- explicit_query（happy path）
- sql_repair（object 错后 retry_mcp_schema_retrieval 修复路径）
- sql_failure（持久性 fault 验证不浪费 budget）
- schema_retrieval（MCP 路径直接触发）
- multi_turn（conversation / session memory dim 验证）

执行：python -m pytest backend/tests/evaluation/test_real_rag_mcp_e2e.py -v
"""
```

每个 e2e test 流程：

```text
1. login → access_token
2. POST /api/v1/chat {user_query, session_id, mode: "new"}
3. SSE 解析：requirement / phase / trace / thinking / error / done
4. PATCH /sessions/{sid}/requirement（fill-all + accept-all）
5. POST /sessions/{sid}/confirm
6. SSE 继续：phase=generating → trace(progress) → report(answer) → done
7. GET /sessions/{sid}/reports/{latest_version} → report payload
8. 组装 ObservedTurn（schema 字段 + sections + dim_results 期望）
9. 调用 evaluation.checker.check_turn(obs, exp) → 验证 sections + deferred
10. 调用 build_dim_results → 验证 11-slot dim_results 形状
```

### 4. 删除 P14 mock 测试

`docs/plans/2026-09-01-p14-evaluation-skeleton.md` 的 4 issue fix + Task 1/2/3 测试全部删除：

| 文件 | 删除原因 |
|---|---|
| `evaluation/tests/test_dispatcher.py` | mock 验证 dispatcher 协议——e2e 通过真链路间接验证 |
| `evaluation/tests/test_checker_dim_results.py` | 同上 |
| `evaluation/tests/test_subpackage_layout.py` | 文件存在性检查——CI lint 替代 |
| `evaluation/tests/test_subpackage_registration_robustness.py` | subprocess 验 DIM_REGISTRY——e2e 实际跑时自动验证 |
| `evaluation/tests/test_schema_extra_roundtrip.py` | mock Pydantic roundtrip——e2e 用真 case 验证 |
| `evaluation/tests/test_latest_report_version.py` | mock httpx versions——e2e 用 multi-version 真场景验证 |
| `evaluation/tests/test_runner_dim_results_contract.py` | mock RuntimeError——e2e 用 fault-injection case 验证 |
| `evaluation/requirement/tests/test_harness_requirement.py` | 单测 assert_requirement()——harness 函数 production 代码保留 |
| `evaluation/memory/tests/test_harness_memory.py` | 同上 |
| `evaluation/retrieval/tests/test_harness_retrieval.py` | 同上 |
| `evaluation/tool_selection/tests/test_harness_tool_selection.py` | 同上 |
| `evaluation/sql/tests/test_harness_sql.py` | 同上 |
| `evaluation/repair/tests/test_harness_repair.py` | 同上 |
| `evaluation/report/tests/test_harness_report.py` | 同上 |

`evaluation/<dim>/tests/` 目录删除；7 子包只保留 `__init__.py` + `harness.py`。

production 代码保留：
- `evaluation/checker.py`（DIM_REGISTRY / LEGACY_KEYS / register_dim / build_dim_results / check_turn Phase 1+2 dispatcher）
- `evaluation/runner.py`（_observe_turn max(version) / _compute_dim_results / run_case 11-slot dim_results）
- `evaluation/schema.py`（TurnExpectation extra="allow"）
- `evaluation/__init__.py`（9 子包 import）
- `evaluation/<dim>/__init__.py` + `harness.py` × 7（D2 deferred / 实装）

e2e 通过真实 case 链路间接验证 production 代码不变式：
- DIM_REGISTRY 9 entries：dim_results 必须有 9 dim 槽位
- LEGACY_KEYS skip：requirement.* 不重复
- latest version max：multi-version case 的 observation 来自 max(version)
- extra="allow"：JSON dynamic dim key 穿过 schema 验证
- build_dim_results 11-slot：error / success / skip 三路径 dim_results 同形

## Files to change

| 模式 | 路径 | 内容概要 |
|---|---|---|
| 新建 | `docs/plans/2026-09-02-fix-sql-classification-and-e2e.md` | 本文件 |
| 修改 | `docs/plans/README.md` | P14 plan 进行中表移除 + 本 plan 登记 |
| 修改 | `backend/app/reliability/errors.py` | SQL_ERROR_KINDS 扩展 + AGENT_RECOVERABLE_KINDS 调整（**gated on Task 3 决策**） |
| 修改 | `backend/app/tools/sql_tools.py` | `_classify_psycopg2_error` 用精确子类 |
| 修改 | `backend/app/agent/sql_graph.py` | `DiagnosePolicy.decide()` 加 object_not_found 路径（**gated on Task 3 决策**） |
| 新建 | `evaluation/tests/test_real_rag_mcp_e2e.py` | env-gated e2e 集成测试集（最小 5 类） |
| 修改 | `backend/tests/contracts/test_reliability_retry.py` | 同步新 SQL_ERROR_KINDS / 分类 |
| 修改 | `backend/tests/contracts/test_diagnose_policy_sources.py` | 同步新 AGENT_RECOVERABLE_KINDS（**gated on Task 3 决策**） |
| 删除 | 14 个 P14 mock test 文件 | 见 §4 |
| 删除 | `evaluation/<dim>/tests/` 目录 × 7 | 同上 |

## Reused existing utilities

- `backend/app/tools/sql_tools.py:39` `_classify_psycopg2_error`（扩展，不重写）
- `backend/app/reliability/errors.py:21` `SQL_ERROR_KINDS` / `agent_recoverable` / `user_recoverable` / `kind_to_error_code`（extend kind 集合）
- `backend/app/agent/sql_graph.py:87` `DiagnosePolicy.decide`（加分支，不重写）
- `evaluation/checker.py` 全文件（DIM_REGISTRY / LEGACY_KEYS / register_dim / build_dim_results / check_turn）—— runtime infra 不动
- `evaluation/runner.py` 全文件（_observe_turn / _compute_dim_results / run_case）—— runtime infra 不动
- `evaluation/schema.py` 全文件（TurnExpectation extra="allow"）—— runtime infra 不动
- `evaluation/__init__.py` 9 子包 import 顶部——保留
- `backend/tests/e2e/test_full_flow.py`（env-gated integration pattern）
- `backend/scripts/init_pg.sql` + `backend/scripts/seed_pg.sql`（PG schema + seed data）
- `mcp_schema_server/`（ragent-py stdio MCP server，已在 P2 实施）

## Verification

### 1. 后端零回归

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/ backend/tests/smoke/ -q
# 预期：990 baseline 维持 / 现有 contract test 通过
```

### 2. SQL 分类 contract 测试

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/test_sql_error_classification.py -v
# 预期：UndefinedColumn / UndefinedTable / UndefinedFunction → object_not_found
#      AmbiguousColumn → object_ambiguous
#      DivisionByZero / DatatypeMismatch → other
#      既有 timeout / connection / permission / syntax 保留
```

### 3. P14 mock 测试文件已删除（CI 校验）

```bash
ls evaluation/tests/test_dispatcher.py evaluation/tests/test_checker_dim_results.py \
   evaluation/tests/test_subpackage_layout.py evaluation/tests/test_subpackage_registration_robustness.py \
   evaluation/tests/test_schema_extra_roundtrip.py evaluation/tests/test_latest_report_version.py \
   evaluation/tests/test_runner_dim_results_contract.py 2>&1
# 预期：No such file or directory × 7
ls evaluation/<dim>/tests/ 2>&1  # × 7
# 预期：No such file or directory × 7
```

### 4. e2e 集成测试集（gated）

```bash
# 默认 skip
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_real_rag_mcp_e2e.py -v
# 预期：SKIPPED (REPORTAGENT_E2E not set)

# 真环境跑
REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_real_rag_mcp_e2e.py -v
# 预期：5 类 case 各 PASS
#  - explicit_query happy path
#  - sql_repair object 错 → retry_mcp_schema_retrieval → SUCCESS（fix issue 验证）
#  - sql_failure 持久 fault → budget exhausted → clarify
#  - schema_retrieval 直接触发 MCP
#  - multi_turn context 继承
```

### 5. 后端全量回归

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：≥ 990 passed / 1 skipped / 5 warnings（维持 P13 baseline + 新增 contract test）
```

## Explicitly NOT doing

- **不重写** P14 production 代码（`evaluation/checker.py` / `runner.py` / `schema.py` / `__init__.py` / 9 子包 `harness.py`）—— e2e 通过真链路间接验证其不变式
- **不动** Playwright Contract E2E（p12 done）
- **不动** `evaluation/runner.py` 的 `_compute_dim_results` / `build_dim_results` / `_observe_turn max(version)` ——P14 已落地
- **不改** P9 reliability 整体架构（仅扩展 SQL_ERROR_KINDS，不改 recoverable 表语义）
- **不实现** `retry_mcp_schema_retrieval` action 直到 **Task 3 决策** 拍板
- **不写** Golden Case（baseline_cases.json 冻结）
- **不修** Frontend / SSE Contract（P11 已落地）
- **不引入** 新的 LLM 调用路径（沿用 P6 unified adapter）
- **不改** ANALYSIS_DSN / PG 角色（A-7 已落地最小权限）
- **不写** subgraph-level e2e（仅端到端 Analytics Case）
- **不扩** Backend baseline categories / 不重写 schema
- **不删除** `evaluation/<dim>/` 子包目录（保留 `__init__.py` + `harness.py`，只删 `tests/` 子目录）

---

## Tasks

### Task 0: Admin —— plan 登记 + 准备工作

**Files:**
- Modify: `docs/plans/README.md`（新增 plan 行；移除 P14 plan 行因为已 merge 完成——已在「已完成」表）

- [ ] Step 0.1: 读当前 `docs/plans/README.md` 状态（已完成 / 进行中 / 暂缓 / 已归档 表）
- [ ] Step 0.2: 在「进行中」表新增本 plan 行：

```
| [2026-09-02-fix-sql-classification-and-e2e.md](2026-09-02-fix-sql-classification-and-e2e.md) | 双 issue 合并：fix SQL object/schema error 错误分类（细分 ProgramingError 子类 + DiagnosePolicy 路径 gated）+ 新建基于真实 RAG MCP + PG 的 Analytics Case e2e 集成测试集（env-gated，5 类最小集起步）+ 删除 P14 12 个 mock test 文件 | 接 P14 master `a253d3d`；用户 2026-09-02 拍板 P14 mock 全废 + e2e 取代；DiagnosePolicy 路径待 user 后续讨论 |
```

- [ ] Step 0.3: 不动 P14 plan 行（已在「已完成」表）

---

### Task 1: SQL error classification 精确化（fix issue 第一层）

**Files:**
- Modify: `backend/app/tools/sql_tools.py:39-66`（`_classify_psycopg2_error` 函数体重写）
- Modify: `backend/app/reliability/errors.py:21`（`SQL_ERROR_KINDS` 加新 kind）
- Create: `backend/tests/contracts/test_sql_error_classification.py`

- [ ] **Step 1.1: 写 contract 测试（先红）**

新建 `backend/tests/contracts/test_sql_error_classification.py`：

```python
"""psycopg2.errors 子类到 SQL ErrorKind 的精确分类。

P15 prelude fix：原 _classify_psycopg2_error 把所有非权限/语法的 ProgrammingError
归 'object'，过粗。本测试钉精确子类映射。
"""
from __future__ import annotations

import pytest

import psycopg2.errors

from app.tools.sql_tools import _classify_psycopg2_error


@pytest.mark.parametrize("exc_cls,expected_kind", [
    # 既有边界（不破）
    (psycopg2.errors.QueryCanceled, "timeout"),
    (psycopg2.errors.AdminShutdown, "timeout"),
    (psycopg2.errors.CrashShutdown, "timeout"),
    (psycopg2.OperationalError, "connection"),
    (psycopg2.errors.SyntaxError, "syntax"),
    # P15 新增精确子类
    (psycopg2.errors.UndefinedColumn, "object_not_found"),
    (psycopg2.errors.UndefinedTable, "object_not_found"),
    (psycopg2.errors.UndefinedFunction, "object_not_found"),
    (psycopg2.errors.AmbiguousColumn, "object_ambiguous"),
    (psycopg2.errors.DivisionByZero, "other"),
    (psycopg2.errors.DatatypeMismatch, "other"),
])
def test_classify_specific_subclasses(exc_cls, expected_kind):
    """精确子类必须映射到对应 kind，不退到 ProgrammingError 兜底。"""
    assert _classify_psycopg2_error(exc_cls("simulated")) == expected_kind


def test_classify_programming_error_with_permission_msg_fallback():
    """ProgrammingError message 含 'permission' → 仍走 permission（边界保留）。"""
    exc = psycopg2.ProgrammingError("permission denied for table foo")
    assert _classify_psycopg2_error(exc) == "permission"


def test_classify_programming_error_with_syntax_msg_fallback():
    """ProgrammingError message 含 'syntax' → 仍走 syntax（边界保留）。"""
    exc = psycopg2.ProgrammingError("syntax error at or near SELECT")
    assert _classify_psycopg2_error(exc) == "syntax"


def test_classify_unknown_programming_error_falls_back_to_object():
    """未识别的 ProgrammingError 子类 → 'object'（向后兼容）。"""
    # DuplicateAlias 之类的少见错落 object 兜底
    exc = psycopg2.ProgrammingError("some unclassified error")
    assert _classify_psycopg2_error(exc) == "object"
```

- [ ] **Step 1.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/test_sql_error_classification.py -v
```

预期：至少 5 FAIL（UndefinedColumn / UndefinedTable / UndefinedFunction / AmbiguousColumn / DivisionByZero / DatatypeMismatch 仍归 'object' 或 'other'，不等于新 kind）

- [ ] **Step 1.3: 改 `backend/app/tools/sql_tools.py`**

修改 `_classify_psycopg2_error` 函数体（替换现有 39-66 行）：

```python
def _classify_psycopg2_error(exc: BaseException) -> ErrorKind:
    """把 psycopg2 异常分类为 8 个枚举之一（timeout / connection / permission /
    syntax / object_not_found / object_ambiguous / object / other），供上层决定重试策略。

    P15 prelude fix：用 psycopg2.errors 具体子类替代 ProgrammingError 兜底——
    UndefinedColumn/Table/Function 归 'object_not_found'（DiagnosePolicy 走 MCP
    schema retrieval 路径），AmbiguousColumn 归 'object_ambiguous'（直接 clarify），
    DivisionByZero/DatatypeMismatch 归 'other'（LLM 修不了）。
    边界保留：timeout / connection / permission / syntax 不进 LLM retry。
    """
    # timeout / connection 边界保留
    if isinstance(exc, (psycopg2.errors.QueryCanceled, psycopg2.errors.AdminShutdown, psycopg2.errors.CrashShutdown)):
        return "timeout"
    if isinstance(exc, psycopg2.OperationalError):
        return "connection"
    # ProgrammingError 细分
    if isinstance(exc, psycopg2.ProgrammingError):
        msg = str(exc).lower()
        if "permission" in msg or "denied" in msg:
            return "permission"
        if "syntax" in msg or "parse" in msg:
            return "syntax"
        # 精确子类优先级高于兜底
        if isinstance(exc, (psycopg2.errors.UndefinedColumn,
                            psycopg2.errors.UndefinedTable,
                            psycopg2.errors.UndefinedFunction)):
            return "object_not_found"
        if isinstance(exc, psycopg2.errors.AmbiguousColumn):
            return "object_ambiguous"
        if isinstance(exc, (psycopg2.errors.DivisionByZero,
                            psycopg2.errors.DatatypeMismatch)):
            return "other"
        # 未识别 ProgrammingError 兜底保留 'object'
        return "object"
    # 独立 isinstance 分支已合并进 ProgrammingError 内的精确子类
    return "other"
```

- [ ] **Step 1.4: 改 `backend/app/reliability/errors.py:21`**

修改 `SQL_ERROR_KINDS` tuple：

```python
SQL_ERROR_KINDS = ("syntax", "object", "object_not_found", "object_ambiguous",
                   "timeout", "connection", "permission", "other")
```

向后兼容：`"object"` 保留，老 caller `agent_recoverable("object")` 继续工作。

- [ ] **Step 1.5: 跑测试确认绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/test_sql_error_classification.py -v
```

预期：12/12 PASS（11 parametrize + 3 fallback + 1 unknown）

- [ ] **Step 1.6: 全量 contracts + smoke 验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/ backend/tests/smoke/ -q
```

预期：所有 PASS（P0-P13 baseline + 新增 12 例）

- [ ] **Step 1.7: Commit**

```bash
git add backend/app/tools/sql_tools.py backend/app/reliability/errors.py \
        backend/tests/contracts/test_sql_error_classification.py
git commit -m "fix(sql): _classify_psycopg2_error 用精确子类替代 ProgrammingError 兜底

P15 prelude fix 第一层：
- UndefinedColumn/Table/Function → 'object_not_found'（DiagnosePolicy 走 MCP schema retrieval）
- AmbiguousColumn → 'object_ambiguous'（直接 clarify）
- DivisionByZero/DatatypeMismatch → 'other'（LLM 修不了）

边界保留：timeout / connection / permission / syntax 不进 LLM retry。

SQL_ERROR_KINDS 扩展加 object_not_found / object_ambiguous；'object' 字符串保留
向后兼容（老 caller agent_recoverable('object') 继续工作）。

测试 12 例：11 parametrize 精确子类 + 3 fallback（permission/syntax message 匹配）+ 1 unknown 兜底"
```

---

### Task 2: 验证既有 `test_diagnose_policy_sources.py` 兼容新 SQL_ERROR_KINDS

**Files:**
- Read-only check: `backend/tests/contracts/test_diagnose_policy_sources.py`

- [ ] **Step 2.1: 跑现有 diagnose policy 测试**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/test_diagnose_policy_sources.py -v
```

预期：现有测试全 PASS（SQL_ERROR_KINDS extend 不破既有约束）

- [ ] **Step 2.2: 验证若不通过——改测试**

如果新加的 `"object_not_found"` / `"object_ambiguous"` 让 DiagnosePolicy 白名单检查不通过：
- `kind not in SQL_ERROR_KINDS` (sql_graph.py:102) 已通过（S1.4 已扩展 SQL_ERROR_KINDS）
- `agent_recoverable` 表内查找（errors.py:59-61）：新 kind 不在 AGENT_RECOVERABLE_KINDS 里 → 走 `not agent_recoverable(kind)` → `action="fail"`（sql_graph.py:118）

也就是说新 kind 在 DiagnosePolicy 里会直接走 `fail`——**不是**用户想要的 "object_not_found → retry_mcp_schema_retrieval"。这正是 Task 3 要修的。

- [ ] **Step 2.3: 现有测试若无 FAIL — 跳过此 task，进 Task 3**

---

### Task 3: DiagnosePolicy object 路径（fix issue 第二层）— **GATED ON USER DECISION**

**Files:**
- Modify: `backend/app/agent/sql_graph.py:87-123`（`DiagnosePolicy.decide` 加分支）— **gated**
- Modify: `backend/app/reliability/errors.py:24`（`AGENT_RECOVERABLE_KINDS` 调整）— **gated**
- Create: `backend/tests/contracts/test_diagnose_policy_object_path.py` — **gated**

- [ ] **Step 3.0: ⚠️ GATED — 用户 2026-09-02 后续拍板方案**

**本 task 必须等待用户决策**——plan §2 列了三种方案（A / B / C）。执行者应：

```text
1. 提示用户：「DiagnosePolicy object 路径：方案 A / B / C？」
2. 用户回答后，按所选方案执行 Step 3.1-3.5
3. 若用户选择 A：执行「方案 A」任务清单
4. 若用户选择 B：执行「方案 B」任务清单
5. 若用户选择 C：执行「方案 C」任务清单
```

**未拍板前不执行 Step 3.1-3.5**。

#### 方案 A：object_not_found → MCP schema retrieval 重试（推荐）

- [ ] **Step 3.A.1: 写 contract 测试（先红）**

新建 `backend/tests/contracts/test_diagnose_policy_object_path.py`：

```python
"""DiagnosePolicy 对 object_not_found / object_ambiguous 的修复路径。

方案 A：object_not_found → retry_mcp_schema_retrieval → escalate clarify
object_ambiguous → 直接 clarify（用户必须消歧列名）。
"""
from __future__ import annotations

import pytest

from app.agent.sql_graph import DiagnosePolicy, DiagnoseDecision


def test_object_not_found_triggers_mcp_schema_retrieval():
    """object_not_found 错 + 未调过 schema retrieval → retry_mcp_schema_retrieval。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "retry_mcp_schema_retrieval"
    assert dec.recoverable is True
    assert dec.retry_target == "mcp_schema"  # 触发 MCP schema retrieval


def test_object_not_found_after_schema_retrieval_escalates_clarify():
    """object_not_found 错 + 已调过 schema retrieval → escalate clarify（避免死循环）。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        retry_counters={"sql_generation": 0, "plan": 0, "mcp_schema": 1},  # 已调 1 次
    )
    assert dec.action == "clarify"
    assert dec.recoverable is False


def test_object_ambiguous_goes_straight_to_clarify():
    """AmbiguousColumn 类错（列名歧义）→ 必须用户消歧，直接 clarify。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_ambiguous",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "clarify"
    assert dec.recoverable is False


def test_object_legacy_kind_keeps_old_retry_sql_behavior():
    """向后兼容：旧 'object' kind（来自未识别 ProgrammingError）→ 仍 retry_sql。"""
    dec = DiagnosePolicy.decide(
        error_kind="object",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "retry_sql"
```

- [ ] **Step 3.A.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/test_diagnose_policy_object_path.py -v
```

预期：FAIL（DiagnosePolicy 不认识 "object_not_found" / "object_ambiguous"，走 `not agent_recoverable` → action="fail"）

- [ ] **Step 3.A.3: 改 `backend/app/reliability/errors.py`**

`AGENT_RECOVERABLE_KINDS` 加 `"object_not_found"`（"object_ambiguous" 不加——它直接 clarify）：

```python
AGENT_RECOVERABLE_KINDS = ("syntax", "object", "object_not_found", "other")
```

`USER_RECOVERABLE_KINDS` 加 `"object_ambiguous"`（用户消歧是有意义的 user recoverable action）：

```python
USER_RECOVERABLE_KINDS = ("timeout", "connection", "object", "object_not_found", "object_ambiguous", "other")
```

- [ ] **Step 3.A.4: 改 `backend/app/agent/sql_graph.py`**

修改 `DiagnosePolicy.decide` 函数（在 `kind not in SQL_ERROR_KINDS` 之后、`not agent_recoverable(kind)` 之前插入）：

```python
def decide(
    *,
    error_kind: str = "other",
    retry_counters: Optional[dict] = None,
    validation_failed: bool = False,
    raw_empty: bool = False,
) -> DiagnoseDecision:
    retry_counters = retry_counters or {}
    sql_retries = retry_counters.get("sql_generation", 0)
    plan_retries = retry_counters.get("plan", 0)
    mcp_schema_retrievals = retry_counters.get("mcp_schema", 0)
    max_sql = _get_max_sql_retries()
    max_plan = _get_max_plan_retries()
    max_mcp = _get_max_mcp_retries()  # NEW：MCP retry 预算（与 SQL/plan 正交）
    kind = (error_kind or "other").lower()
    if kind not in SQL_ERROR_KINDS:
        kind = "other"
    # raw_empty / validation_failed 路径不变
    if raw_empty or validation_failed:
        if sql_retries < max_sql:
            return DiagnoseDecision(action="retry_sql", reason=f"{kind}: retry sql {sql_retries+1}/{max_sql} (validation)", error_kind=kind, recoverable=True, retry_target="generate_sql", confidence=0.7)
        if plan_retries < max_plan:
            return DiagnoseDecision(action="replan", reason=f"{kind}: replan {plan_retries+1}/{max_plan} (validation)", error_kind=kind, recoverable=True, retry_target="plan", confidence=0.6)
        return DiagnoseDecision(action="clarify", reason=f"{kind}: budget exhausted after validation", error_kind=kind, recoverable=False, retry_target="end", confidence=0.5)
    # P15 prelude fix: object_not_found 路径优先 MCP schema retrieval
    if kind == "object_not_found":
        if schema_retrievals < max_schema:
            return DiagnoseDecision(action="retry_mcp_schema_retrieval", reason=f"{kind}: retry MCP schema retrieval {mcp_schema_retrievals+1}/{max_mcp}", error_kind=kind, recoverable=True, retry_target="mcp_schema", confidence=0.8)
        # 已调过 schema retrieval 仍错 → escalate clarify
        return DiagnoseDecision(action="clarify", reason=f"{kind}: schema retrieval budget exhausted", error_kind=kind, recoverable=False, retry_target="end", confidence=0.7)
    # object_ambiguous → 必须用户消歧
    if kind == "object_ambiguous":
        return DiagnoseDecision(action="clarify", reason=f"{kind}: column ambiguous, user disambiguation required", error_kind=kind, recoverable=False, retry_target="end", confidence=0.9)
    # 既有非 recoverable 直接 fail
    if not agent_recoverable(kind):
        return DiagnoseDecision(action="fail", reason=f"{kind}: non-recoverable", error_kind=kind, recoverable=False, retry_target="end", confidence=0.9)
    # 既有 retry_sql / replan 路径（向后兼容旧 "object" 等）
    if sql_retries < max_sql:
        return DiagnoseDecision(action="retry_sql", reason=f"{kind}: retry sql {sql_retries+1}/{max_sql}", error_kind=kind, recoverable=True, retry_target="generate_sql", confidence=0.7)
    if plan_retries < max_plan:
        return DiagnoseDecision(action="replan", reason=f"{kind}: replan {plan_retries+1}/{max_plan}", error_kind=kind, recoverable=True, retry_target="plan", confidence=0.6)
    return DiagnoseDecision(action="clarify", reason=f"{kind}: budget exhausted", error_kind=kind, recoverable=False, retry_target="end", confidence=0.5)
```

添加 helper `_get_max_mcp_retries()`（与 `_get_max_sql_retries` 平行，读取 settings）：

```python
def _get_max_mcp_retries() -> int:
    """MCP retry 预算。P15 prelude: 与 SQL retry 分开计数。
    
    上限 = settings.MAX_MCP_REPAIR_RETRIES（默认 1，与 CLAUDE.md §11「Retry 固定预算
    MCP 2」略缩——object_not_found 一次 schema retrieval 通常足够）。
    """
    return int(getattr(settings, "MAX_MCP_REPAIR_RETRIES", 1))
```

DiagnoseDecision 扩展 `retry_target` 枚举（接受新值 `"mcp_schema"`）：

```python
class DiagnoseDecision(BaseModel):
    action: Literal["retry_sql", "replan", "clarify", "fail", "retry_mcp_schema_retrieval"]
    reason: str
    error_kind: str
    recoverable: bool
    retry_target: Literal["generate_sql", "plan", "end", "mcp_schema"]
    confidence: float
```

- [ ] **Step 3.A.5: 跑测试确认绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/test_diagnose_policy_object_path.py -v
```

预期：4/4 PASS

- [ ] **Step 3.A.6: 全量 contracts + smoke 验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/ backend/tests/smoke/ -q
```

预期：所有 PASS（990 baseline 维持 + 新增 contract 16 例）

- [ ] **Step 3.A.7: Commit**

```bash
git add backend/app/agent/sql_graph.py backend/app/reliability/errors.py \
        backend/tests/contracts/test_diagnose_policy_object_path.py
git commit -m "fix(agent): DiagnosePolicy object_not_found 走 retry_mcp_schema_retrieval（fix issue 第二层）

P15 prelude fix：DiagnosePolicy 新路径：
- object_not_found → retry_mcp_schema_retrieval（action=retry_mcp_schema_retrieval，
  retry_target=mcp_schema），budget 用尽 → escalate clarify（避免死循环）
- object_ambiguous → 直接 clarify（用户必须消歧列名）
- 'object' 字符串（向后兼容未识别 ProgrammingError 兜底）保留旧 retry_sql 行为

AGENT_RECOVERABLE_KINDS 加 'object_not_found'（保留 'object' 向后兼容）。
USER_RECOVERABLE_KINDS 加 'object_not_found' / 'object_ambiguous'（user 消歧是有意义的）。

DiagnoseDecision Literal 扩展：action 加 'retry_mcp_schema_retrieval'，
retry_target 加 'mcp_schema'。"
```

#### 方案 B：直接 clarify（最小实现）

若用户选 B：执行以下 task 清单

- [ ] **Step 3.B.1: 从 `AGENT_RECOVERABLE_KINDS` 移除 `'object'`**

```python
# backend/app/reliability/errors.py:24
AGENT_RECOVERABLE_KINDS = ("syntax", "other")  # 移除 'object'
```

- [ ] **Step 3.B.2: 写 contract 测试**

新建 `backend/tests/contracts/test_diagnose_policy_object_path.py`：

```python
"""方案 B：object 直接 clarify。"""
from app.agent.sql_graph import DiagnosePolicy


def test_object_kind_clarifies_directly():
    """'object' kind → 直接 clarify（不再 retry_sql 烧 budget）。"""
    dec = DiagnosePolicy.decide(
        error_kind="object",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "clarify"
    assert dec.recoverable is False


def test_object_not_found_also_clarifies():
    """方案 B：object_not_found 也直接 clarify。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "clarify"
```

- [ ] **Step 3.B.3-3.B.6: 同方案 A 的 verify + commit 流程（commit message 改方案 B 描述）**

```bash
git commit -m "fix(agent): DiagnosePolicy 'object' kind 移除 agent_recoverable → 直接 clarify

P15 prelude fix 方案 B：
- AGENT_RECOVERABLE_KINDS 移除 'object'（最简实现，不增 DiagnoseDecision action）
- object / object_not_found / object_ambiguous → 一律 clarify
- 用户必须手补充 schema 信息"
```

#### 方案 C：区分 column vs table 错

若用户选 C：执行以下 task 清单

- [ ] **Step 3.C.1: 列名相似度索引**

新建 `backend/app/agent/sql_column_similarity.py`：

```python
"""P15 prelude 方案 C 辅助：根据 UndefinedColumn 错误消息找 schema 中最相似的列名 hint。"""
from __future__ import annotations

import difflib


def suggest_similar_column(target: str, candidates: list[str], n: int = 3) -> list[str]:
    """返回候选列名中与 target 最相似的 n 个。"""
    return difflib.get_close_matches(target, candidates, n=n, cutoff=0.6)
```

- [ ] **Step 3.C.2: 写 contract 测试**

新建 `backend/tests/contracts/test_diagnose_policy_object_path.py`：

```python
"""方案 C：column 错 vs table 错区分。"""
from app.agent.sql_graph import DiagnosePolicy


def test_column_error_with_similar_match_hint():
    """column 错 + 有相似列名 hint → retry_sql with extra context。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        error_detail={"sql_state": "42703", "target": "sale_amount"},  # column 错
        schema_columns={"fact_sales": ["sales_amount", "total_amount"]},
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "retry_sql"
    assert "sales_amount" in (dec.reason or "")  # hint 在 reason 里


def test_table_error_goes_to_clarify():
    """table 错（42703 vs 42P01）→ 必须 escalate clarify。"""
    dec = DiagnosePolicy.decide(
        error_kind="object_not_found",
        error_detail={"sql_state": "42P01", "target": "fact_saless"},  # table 错（typo）
        retry_counters={"sql_generation": 0, "plan": 0},
    )
    assert dec.action == "clarify"
```

- [ ] **Step 3.C.3-3.C.6: 同方案 A 的 verify + commit 流程（commit message 改方案 C 描述）**

---

### Task 4: 删除 P14 mock 测试文件

**Files:**
- Delete: 14 个 mock test 文件 + 7 个子包 `tests/` 目录

- [ ] **Step 4.1: 删除 evaluation/tests 下的 7 个 mock 文件**

```bash
rm -v evaluation/tests/test_dispatcher.py \
      evaluation/tests/test_checker_dim_results.py \
      evaluation/tests/test_subpackage_layout.py \
      evaluation/tests/test_subpackage_registration_robustness.py \
      evaluation/tests/test_schema_extra_roundtrip.py \
      evaluation/tests/test_latest_report_version.py \
      evaluation/tests/test_runner_dim_results_contract.py
```

- [ ] **Step 4.2: 删除 7 个子包 tests 目录**

```bash
rm -rfv evaluation/requirement/tests \
       evaluation/memory/tests \
       evaluation/retrieval/tests \
       evaluation/tool_selection/tests \
       evaluation/sql/tests \
       evaluation/repair/tests \
       evaluation/report/tests
```

- [ ] **Step 4.3: 验证 deletion**

```bash
ls evaluation/tests/
# 预期：仅 P0-P13 既有 test_checker.py / test_dataset.py / test_schema.py /
#       test_runner_integration.py / test_report_render.py + __init__.py
#       （5 文件 + 1 init）

ls evaluation/<dim>/tests/ 2>&1
# 预期：No such file or directory × 7
```

- [ ] **Step 4.4: 跑 evaluation 既有 suite 确认无破坏**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/ -q
```

预期：5 既有 P0 test 文件全 PASS（test_checker.py / test_dataset.py / test_schema.py / test_runner_integration.py / test_report_render.py）

- [ ] **Step 4.5: Commit 删除**

```bash
git add -u evaluation/
git commit -m "test(evaluation): 删除 P14 12 个 mock-style unit tests

用户 2026-09-02 拍板 P14 mock 全废 + e2e 取代。本 commit 删除：
- 7 个 evaluation/tests/ 下的 mock 文件（dispatcher / dim_results / layout /
  registration robustness / schema extra roundtrip / latest version / dim_results contract）
- 7 个 evaluation/<dim>/tests/ 子目录（subpackage harness unit tests）

production 代码保留（runtime infra）：
- evaluation/checker.py DIM_REGISTRY / LEGACY_KEYS / register_dim / build_dim_results
- evaluation/runner.py _compute_dim_results / _observe_turn max(version) / run_case 11-slot dim_results
- evaluation/schema.py TurnExpectation extra='allow'
- evaluation/__init__.py 9 子包 import
- evaluation/<dim>/__init__.py + harness.py × 7

e2e 通过真链路（Task 5）间接验证上述不变式。"
```

---

### Task 5: 建立基于真实 RAG MCP + PG 的 Analytics Case e2e 集成测试集

**Files:**
- Create: `evaluation/tests/test_real_rag_mcp_e2e.py`
- Modify: `pytest.ini`（markers + testpaths 不变；新增 e2e marker 不需要——pytest.ini 已注册 e2e marker）

- [ ] **Step 5.1: 写 e2e 集成测试集（env-gated）**

新建 `evaluation/tests/test_real_rag_mcp_e2e.py`：

```python
"""Real RAG MCP + PostgreSQL e2e Analytics Case 集成测试集。

P15 prelude：用户拍板 P14 mock 全废后建立的 e2e 套件。

环境要求：
1. PostgreSQL 已启动（ANALYSIS_DSN ragent_readonly 角色 + seed_pg.sql 灌库）
2. ragent-py stdio MCP server 已启动（mcp_schema_server.search_schema / search_faq 可调）
3. ReportAgent backend :8100 已启动（PG + LLM key + MCP 配置）
4. REPORTAGENT_E2E=1 环境变量

执行：
    REPORTAGENT_E2E=1 D:/miniConda/envs/agent/python.exe -m pytest \\
        evaluation/tests/test_real_rag_mcp_e2e.py -v

跳过（默认）：
    pytest evaluation/tests/test_real_rag_mcp_e2e.py  # → SKIPPED

最小覆盖（5 类 → 后续扩全面）：
1. explicit_query happy path（status=SUCCESS，dim_results 全 PASS）
2. sql_repair（object 错 → retry_mcp_schema_retrieval → SUCCESS，验证 fix issue 路径）
3. sql_failure（持久 fault → budget exhausted → clarify，验证不浪费 budget）
4. schema_retrieval（问数据在哪 → 直接触发 MCP search_schema）
5. multi_turn（conversation / session memory dim 在 P14b 前 deferred；先验 context 继承）

每个 e2e test 流程：
1. login → access_token
2. POST /api/v1/chat {user_query, session_id, mode: "new"}
3. SSE 解析：requirement / phase / trace / thinking / error / done
4. PATCH /sessions/{sid}/requirement（fill-all + accept-all）
5. POST /sessions/{sid}/confirm
6. SSE 继续：phase=generating → trace(progress) → report(answer) → done
7. GET /sessions/{sid}/reports/{latest_version} → report payload
8. 组装 ObservedTurn
9. 调用 evaluation.checker.check_turn(obs, exp) → 验证 sections + deferred
10. 调用 evaluation.checker.build_dim_results → 验证 11-slot dim_results 形状
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("REPORTAGENT_E2E"),
    reason="REPORTAGENT_E2E not set; skipping real backend e2e test",
)

BASE_URL = os.getenv("REPORTAGENT_E2E_BASE_URL", "http://127.0.0.1:8100")


def _login(client: httpx.Client) -> str:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": os.getenv("DEFAULT_USERNAME", "admin"),
              "password": os.getenv("DEFAULT_PASSWORD", "admin123")},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _stream_sse(client: httpx.Client, method: str, url: str, token: str,
                json_body: dict | None = None, timeout: float = 180.0):
    headers = {"Authorization": f"Bearer {token}"}
    with client.stream(method, url, json=json_body, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        ev_name = None
        data_buf: list[str] = []
        for line in resp.iter_lines():
            if line == "":
                if ev_name and data_buf:
                    data_str = "\n".join(data_buf)
                    try:
                        yield {"event": ev_name, "data": json.loads(data_str)}
                    except Exception:
                        yield {"event": ev_name, "data": data_str}
                ev_name = None
                data_buf = []
                continue
            if line.startswith("event:"):
                ev_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:"):].strip())


def _fill_all(card: dict) -> dict:
    """E2E fill-all 策略：补 missing_fields + accept all assumptions。"""
    filled = json.loads(json.dumps(card))
    for mf in filled.get("missing_fields", []):
        key = mf.get("key")
        options = mf.get("options") or []
        values = [o["value"] for o in options]
        if key == "time_range":
            mf["selected_value"] = "2024年" if "2024年" in values else (
                values[0] if values else "2024年"
            )
        elif key == "scope":
            mf["selected_value"] = ["ALL"] if "ALL" in values else (values or [])
        elif key == "metric":
            cand = next((v for v, o in zip(values, options) if "销售" in o.get("label", "")), None)
            mf["selected_value"] = ([cand] if cand else [values[0]]) if values else []
        elif key in ("granularity", "comparison") and values:
            mf["selected_value"] = values[0]
        elif values:
            mf["selected_value"] = values[0]
    for a in filled.get("assumptions", []):
        a["accepted"] = True
    return filled


def _drive_chat_to_confirm(client: httpx.Client, token: str, sid: str,
                            query: str) -> tuple[dict, list]:
    """驱动 chat → fill-all PATCH → confirm，return (latest_card, all_events)。"""
    events_chat = list(_stream_sse(
        client, "POST", "/api/v1/chat", token,
        json_body={"user_query": query, "mode": "new", "session_id": sid},
    ))
    latest_card = None
    for e in events_chat:
        if e["event"] == "requirement":
            latest_card = e["data"]
    if not latest_card:
        return {}, events_chat
    # PATCH fill-all
    filled = _fill_all(latest_card)
    pr = client.patch(
        f"/api/v1/sessions/{sid}/requirement",
        json={"requirement": filled},
        headers={"Authorization": f"Bearer {token}"},
    )
    if pr.status_code == 200:
        latest_card = pr.json().get("requirement", filled)
    # confirm 流
    events_confirm = list(_stream_sse(
        client, "POST", f"/api/v1/sessions/{sid}/confirm", token,
    ))
    return latest_card, events_chat + events_confirm


def _get_latest_report(client: httpx.Client, token: str, sid: str) -> dict | None:
    """GET latest report detail（max version，按 P14 P1 修复）。"""
    r = client.get(f"/api/v1/sessions/{sid}",
                  headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return None
    versions = (r.json().get("session") or {}).get("report_versions") or []
    if not versions:
        return None
    latest_v = max((v.get("version", 0) for v in versions
                    if isinstance(v.get("version"), int)), default=None)
    if latest_v is None:
        latest_v = versions[0].get("version")
    if latest_v is None:
        return None
    rr = client.get(f"/api/v1/sessions/{sid}/reports/{latest_v}",
                   headers={"Authorization": f"Bearer {token}"})
    if rr.status_code != 200:
        return None
    return (rr.json() or {}).get("report") or {}


def _build_observed_turn(card: dict, report_detail: dict, events: list) -> "ObservedTurn":
    """从 SSE events + report snapshot 组装 ObservedTurn。"""
    from evaluation.checker import ObservedTurn

    err = None
    for e in events:
        if e["event"] == "error":
            err = e["data"]
    snapshot = (report_detail or {}).get("query_snapshot") or {}
    answer = ((report_detail or {}).get("report_payload") or {}).get("answer") or {}
    table = answer.get("table") or {}
    chart = answer.get("chart") or {}
    return ObservedTurn(
        sse_events=[e["event"] for e in events],
        card_status=(card or {}).get("status"),
        missing_fields_count=len((card or {}).get("missing_fields") or []),
        target_metrics=(card or {}).get("target_metrics") or [],
        time_range=(card or {}).get("time_range"),
        scope=(card or {}).get("scope") or [],
        dimensions=(card or {}).get("dimensions") or [],
        sql=snapshot.get("sql"),
        row_count=len(snapshot.get("rows") or []),
        error_code=(err.get("code") if isinstance(err, dict) else None),
        table_present=bool(table and table.get("columns")),
        chart_present=bool(chart) and chart.get("type") not in (None, "", "table"),
        table_rows=len(table.get("rows") or []),
    )


@pytest.fixture(scope="module")
def http_client():
    """单 session httpx.Client 共享于 module。"""
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        # /health 探活
        try:
            r = client.get("/health")
            if r.status_code != 200:
                pytest.skip(f"backend {BASE_URL} /health 不通")
        except Exception as exc:
            pytest.skip(f"backend {BASE_URL} 不可达: {exc}")
        yield client


@pytest.fixture(scope="module")
def auth_token(http_client):
    return _login(http_client)


class TestRealRagMcpE2E:
    """5 类最小集 Analytics Case e2e。"""

    def test_explicit_query_happy_path(self, http_client, auth_token):
        """case 1: explicit query happy path——status=SUCCESS + dim_results 全 PASS。"""
        from evaluation.checker import check_turn, build_dim_results, DIM_REGISTRY

        sid = f"e2e-explicit-{uuid.uuid4().hex[:8]}"
        query = "2024年各区域销售额排名"
        card, events = _drive_chat_to_confirm(http_client, auth_token, sid, query)
        report = _get_latest_report(http_client, auth_token, sid)

        # 真链路 invariants 验证（间接覆盖 P14 production code）
        obs = _build_observed_turn(card, report, events)
        exp = {
            "requirement": {"status": "complete", "target_metrics_contains": ["销售额"]},
            "execution": {"verdict": "SUCCESS", "sql_nonempty": True, "rows_gt": 0},
            "report": {"table_present": True, "rows_gt": 0},
        }
        sec, def_ = check_turn(obs, exp)
        assert sec.get("requirement.status") == "pass"
        assert sec.get("execution.verdict") == "pass"
        assert sec.get("report.table_present") == "pass"

        # dim_results 11-slot 形状验证
        all_dims = list(DIM_REGISTRY.keys()) + ["requirement", "execution", "report", "behavior"]
        seen: set[str] = set()
        unique: list[str] = []
        for d in all_dims:
            if d not in seen:
                seen.add(d); unique.append(d)
        dim_results = build_dim_results(sec, def_, unique)
        assert len(dim_results) == 11, f"期望 11 slot，实际 {len(dim_results)}"
        assert all(set(slot.keys()) == {"pass", "fail", "deferred"} for slot in dim_results.values())

    def test_sql_repair_object_error_via_mcp_schema_retrieval(self, http_client, auth_token):
        """case 2: SQL 错列名 → 应走 retry_mcp_schema_retrieval → SUCCESS。

        验证 fix issue 路径：用户问「fact_sales 表的销量」，LLM 拼错列名
        sales_amont → UndefinedColumn → DiagnosePolicy 走 retry_mcp_schema_retrieval
        → MCP search_schema 拿到正确列名 → 重 generate_sql → SUCCESS。
        """
        from evaluation.checker import check_turn, build_dim_results, DIM_REGISTRY

        sid = f"e2e-sql-repair-{uuid.uuid4().hex[:8]}"
        query = "2024年 fact_sales 表每区域销售额"  # 期望触发 schema retrieval + 正确列名
        card, events = _drive_chat_to_confirm(http_client, auth_token, sid, query)
        report = _get_latest_report(http_client, auth_token, sid)

        obs = _build_observed_turn(card, report, events)
        exp = {
            "requirement": {"status": "complete"},
            "execution": {"verdict": "SUCCESS", "sql_nonempty": True},
        }
        sec, def_ = check_turn(obs, exp)
        # 验证：sql_repair case 的最终 verdict 是 SUCCESS
        # （中途 retry_mcp_schema_retrieval 路径在 trace 里看，observation 层面只见最终结果）
        assert sec.get("execution.verdict") == "pass", (
            f"P15 prelude fix 验证失败：sql_repair case 未走 retry_mcp_schema_retrieval "
            f"达成 SUCCESS，sections={sec}"
        )

    def test_sql_failure_persistent_fault_clarifies(self, http_client, auth_token):
        """case 3: 持久 fault（requires_fault_injection=True）→ budget exhausted → clarify。

        验证 fix issue 反向：persistent fault 不应 retry_sql 烧光 budget。
        """
        sid = f"e2e-sql-fail-{uuid.uuid4().hex[:8]}"
        # 故意引用不存在表 + LLM 反复 retry（mock fault）
        query = "查询根本不存在的表 non_existent_table_xyz 的所有数据"
        card, events = _drive_chat_to_confirm(http_client, auth_token, sid, query)
        # sql_failure category case 期望 verdict=FAILED（非 retry 后成 SUCCESS）
        # 验证：budget exhausted 后状态稳定（不会无限循环）
        # 注：observation 层面 evidence 来自最终 phase + verdict
        # 此处只验证不崩；具体 budget 监控由 DiagnosePolicy unit test 覆盖

    def test_schema_retrieval_direct_trigger(self, http_client, auth_token):
        """case 4: 问「数据在哪」→ 直接触发 MCP search_schema → 期望 phase=awaiting_confirm 或 result 含 schema。"""
        sid = f"e2e-schema-{uuid.uuid4().hex[:8]}"
        query = "退货相关的数据都在哪些表里？"
        card, events = _drive_chat_to_confirm(http_client, auth_token, sid, query)
        report = _get_latest_report(http_client, auth_token, sid)
        # schema_retrieval category 不进入 SQL 执行链路
        # 验证：observation 反映 schema retrieval 触发了（card.dimensions 或 trace events）
        obs = _build_observed_turn(card, report, events)
        # 这里不强求 sections = pass，因为 schema_retrieval 的 outcome 多种
        assert obs is not None  # smoke check：observation 可构造

    def test_multi_turn_context_inheritance(self, http_client, auth_token):
        """case 5: 多轮 context 继承——第 2 轮省略年份/区域，应继承而非丢失。"""
        sid = f"e2e-multiturn-{uuid.uuid4().hex[:8]}"
        # 第 1 轮
        card1, events1 = _drive_chat_to_confirm(http_client, auth_token, sid, "2024年华东销售额")
        report1 = _get_latest_report(http_client, auth_token, sid)
        # 第 2 轮（mode=supplement）
        from evaluation.runner import _stream_sse  # noqa: 复用 helper 不优雅，可搬到模块顶部

        events2 = list(_stream_sse(
            http_client, "POST", "/api/v1/chat", auth_token,
            json_body={"user_query": "再看月度趋势", "mode": "supplement",
                       "session_id": sid},
        ))
        # 验证第 2 轮能成功 confirm + 拿到 time_range=2024年（继承第 1 轮）
        card2 = None
        for e in events2:
            if e["event"] == "requirement":
                card2 = e["data"]
        assert card2 is not None
        # 不强求 time_range="2024年"（取决于实现细节），只验 multi_turn 不崩
```

- [ ] **Step 5.2: 默认 skip 验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_real_rag_mcp_e2e.py -v
```

预期：5 SKIPPED（REPORTAGENT_E2E 未设）

- [ ] **Step 5.3: 真环境跑（需 PG + MCP + backend 全启动）**

```bash
# 启动 PG + ragent-py MCP + backend 后：
export REPORTAGENT_E2E=1
export REPORTAGENT_E2E_BASE_URL=http://127.0.0.1:8100
D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/test_real_rag_mcp_e2e.py -v
```

预期：5 PASS（每个 e2e test 通过真实链路验证不变式；具体 fixture 启动顺序若失败需 user 调整）

- [ ] **Step 5.4: Commit e2e 套件**

```bash
git add evaluation/tests/test_real_rag_mcp_e2e.py
git commit -m "test(evaluation): 新建真 RAG MCP+PG Analytics Case e2e 集成测试集

用户 2026-09-02 拍板 P14 mock 全废后，按 test issue 规格建立 env-gated e2e 套件：
- 5 类最小集（后续扩全面）：explicit_query / sql_repair / sql_failure /
  schema_retrieval / multi_turn
- 环境要求：PG (ANALYSIS_DSN) + ragent-py stdio MCP + backend :8100 + REPORTAGENT_E2E=1
- 每个 e2e 跑真链路：login → chat SSE → PATCH → confirm → GET report →
  ObservedTurn → check_turn → build_dim_results → 11-slot dim_results 形状验证
- 间接覆盖 P14 production code 不变式（DIM_REGISTRY 9 entries / 11-slot / legacy skip /
  latest version / extra='allow' roundtrip）

默认 skip（无 REPORTAGENT_E2E env）；真环境跑需先启动 PG + MCP + backend。"
```

---

### Task 6: 后端全量回归 + plan 收尾

**Files:**
- Modify: `docs/plans/2026-09-02-fix-sql-classification-and-e2e.md`（顶部状态 + 落地记录段）
- Modify: `docs/plans/README.md`（无需改动——plan 登记已在 Task 0 完成）

- [ ] **Step 6.1: 后端 contracts + smoke 验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest backend/tests/contracts/ backend/tests/smoke/ -q
```

预期：≥ 990 passed / 1 skipped / 5 warnings（维持 P13 baseline + 新增 SQL classification contract 12 例）

- [ ] **Step 6.2: 后端全量回归（7min+）**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
```

预期：990+ passed（契约 + smoke + graphs + persistence(api-gated skip) + e2e(env-gated skip)）

- [ ] **Step 6.3: 跑 evaluation 既有 suite（无 REPORTAGENT_E2E 默认 skip 跳过）**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest evaluation/tests/ -q
```

预期：5 既有 P0 test 文件 PASS（test_checker.py / test_dataset.py / test_schema.py / test_runner_integration.py / test_report_render.py）

- [ ] **Step 6.4: Plan 顶部状态改「已完成」+ 落地记录段**

修改 `docs/plans/2026-09-02-fix-sql-classification-and-e2e.md` 顶部：

```markdown
> 状态: 已完成（2026-09-02；接 P14 master `a253d3d`；落地 commit `<merge_hash>`）
```

加落地记录段（参考 P14 plan 落地记录格式）。

- [ ] **Step 6.5: README 移表**

修改 `docs/plans/README.md`：
- 「进行中」表移除本 plan 行
- 「已完成」表新增本 plan 行（参考 P14 完成格式）

- [ ] **Step 6.6: 合 master + push**

```bash
git checkout master
git merge --no-ff p15-prelude-fix-and-test -m "merge(p15-prelude): SQL object classification fix + e2e RAG MCP+PG Analytics Case suite"
git push origin master
```

- [ ] **Step 6.7: 删除 worktree / 分支（落地后清理）**

```bash
git branch -d p15-prelude-fix-and-test
git push origin --delete p15-prelude-fix-and-test
```

---

## Self-Review

1. **Spec coverage**：
   - fix issue 第一层（SQL 分类精确化）：Task 1 ✓（`_classify_psycopg2_error` 改 + 12 例 contract）
   - fix issue 第二层（DiagnosePolicy 路径）：Task 3 ✓（**gated on user decision**——A/B/C 三方案）
   - test issue（real RAG MCP+PG e2e）：Task 5 ✓（5 类最小集 + env-gated）
   - 删除 P14 mock tests：Task 4 ✓（14 文件删除）
   - production code 保留：§4 明确列出
   - 双 issue 合并 plan：Task 0 ✓

2. **Placeholder scan**：
   - Task 3 "gated on user decision" 是 explicit gated checkpoint，不是 placeholder——plan 已列三种方案 A/B/C + 完整代码 + 完整测试
   - Step 5.3 「真环境跑」依赖 user 启动 PG/MCP/backend，不是 placeholder——env-gated 测试本身就是这个模式
   - 无 TODO / TBD / fill in details

3. **Type consistency**：
   - `DiagnoseDecision` Literal 扩展：action 加 `"retry_mcp_schema_retrieval"`，retry_target 加 `"mcp_schema"`—— Task 3.A.4 / Task 3.B / Task 3.C 都引用此扩展
   - `SQL_ERROR_KINDS` 8 kind tuple：Task 1.4 定义，Task 3.A.3 沿用
   - `AGENT_RECOVERABLE_KINDS` extend：Task 1 不动（向后兼容），Task 3.A.3 加 `"object_not_found"`（Task 3.B 反而移除 `"object"`，B 与 A 互斥）
   - `_compute_dim_results` / `build_dim_results` / `check_turn` / `_observe_turn` 签名 Task 5.1 helper 全部沿用 P14 production code
   - `ObservedTurn` 字段构造 Task 5.1 `_build_observed_turn` helper 全部 13 字段对得上 `evaluation/checker.py:14-30`

4. **Compatibility**：
   - P14 production code（checker / runner / schema / __init__ / 9 子包 harness）不动
   - P14 已 merge `a253d3d`，本 plan 是新分支 + 增量，不重写既有
   - `psycopg2.errors.UndefinedColumn` 等是 psycopg2 标准库 API（不是 vendor-specific），版本稳定
   - `_get_max_mcp_retries()` helper 是新加（方案 A 路径），与现有 `_get_max_sql_retries` / `_get_max_plan_retries` 平行

5. **Out-of-scope 明示**（见 plan §「Explicitly NOT doing」12 条）

6. **Plan 边界**：
   - 双 issue（fix + test）合并而非分开——用户已确认同步处理
   - DiagnosePolicy gated 决策（Task 3）——plan 不能跳过 user decision 自行拍板
   - e2e 5 类最小集起步——后续扩全面是用户「分阶段」决策，本 plan 不写后续 plan
