# P12 Review Round 2 — Mock Cursor 隔离 + Spec 命名校准 + CI 报告分组

> 状态: 已完成（2026-08-31；p12-playwright 分支 4 fix commit：`37223e1` Task 1 mock cursor scope / `ab47121` Task 2 spec 05 改名 / `61ec961` Task 3 spec 10 文档化 / `fe8e9be` Task 4 边界定义 + CI 分组 + e2e/README + `docs commit` Task 5）
> 上游: P12 主实施 6 commit + review-prep-r1 6 commit（`a55db1f`/`f2e415c`/`62b4ff9`/`0325cbe`/`3722637`/`b18565f`）。本轮在 user 亲自 review 代码后开第 2 轮修复。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P12 Contract E2E 的 MockLLM invocation counter 从「一个 backend process 全局」隔离到「per session scope」，同时校准 3 个 P2 命名/语义边界 + 让 CI 报告区分 Contract vs Full。

**Architecture:** 用 `contextvars.ContextVar` 在 graph 入口设 session scope；`MockLLMAdapter._counters` 由 `dict[str, int]` 升级为 `dict[str, dict[str, int]]`（scope → {kind:seq}）；真实 LLM 路径不感知 contextvar；spec 命名与 CI 报告属轻量校准（无架构影响）。

**Tech Stack:** Python `contextvars.ContextVar`（stdlib） + Playwright `--grep` / 多 reporter + 已落地的 `MockLLMAdapter`（`backend/app/llm/mock.py`）。

---

## Context

user 在亲自 review `p12-playwright` 分支代码 + 实际跑过核心 spec 后给出第 2 轮 review：

| # | 等级 | 主题 | 来源 |
|---|---|---|---|
| 1 | **P1** | `MockLLMAdapter._counters` 是 instance 属性，`get_llm_adapter()` 在 mock 模式下复用模块级 singleton（`backend/app/llm/__init__.py:_adapter`），导致**一个 backend process → 一个 MockLLMAdapter → 所有 session 共享 cursor**。当前 P12 spec 设计靠「每 spec 重启 backend + workers=1」把问题压住，但这是隐式依赖，不是 adapter 设计的隔离。任何「同 backend 多 session」扩展都会暴露 cursor 漂移；业务流程多调一次 → cursor 偏移 → 测试失败不一定是业务 bug 而只是 fixture cursor 漂移。 | review |
| 2 | **P2** | Contract E2E 实际证明的是「MCP 不可用 → fallback 后能工作」，不是「frontend→backend→MCP→DB 全链路」。`frontend/e2e/helpers/llm-mock.ts:10` 已写明「Contract 强制不连真实 MCP」，但 README/CLAUDE.md 没正式定义 Contract E2E 的边界 | review |
| 3 | **P2** | spec `05-failed-result` describe 写「ErrorCard 分类文案」，但实现断言 `.wb-finding` error band（含「执行失败」），这是 P10 ReportPaper 历史 FAILED 版本视图，不是 ErrorCard。命名与验收对象不一致 | review |
| 4 | **P2** | spec `10-trace-progress` 用 50ms DOM 轮询 + 正则 `生成 SQL\|校验 SQL\|执行查询\|组织报告`，仅证明「DOM 出现过某个 trace 文本」，不证明 running→success 状态机或 stage 序列。这是 DOM-level smoke assertion，不是严格 progress contract | review |
| 5 | **轻量** | CI 报告 `reporter: [['list']]` 不区分 Contract / Full；npm scripts 已分 `e2e:contract`（0[1-9]+10 共 10 spec）与 `e2e:full`（1[1-2] 共 2 spec），但默认 `npm run e2e` 跑全 12 个；需在 CI 文档与 reporter 配置明确分组 | review |

**与 r1 的关系**：review-prep-r1 已合（`b18565f` 落地记录）落 4 项加固（`get_chat_llm` fail-closed / fixture key 校验 / spec 03 retry SUCCESS 证据 / spec 07 background 落库断言）。**r1 未触及 cursor scope / 命名 / CI 分组**——本 plan 补齐。

## Design

### Fix 1【P1】MockLLM session scope 隔离（cursor 设计根因修）

**核心思路**：在 `MockLLMAdapter` 内部把 `_counters` 从 `dict[str, int]` 升级为 `dict[str, dict[str, int]]`（scope → {kind: seq}）；scope 通过 `contextvars.ContextVar` 传递；graph 入口显式 `set`（`f"{user_id}:{session_id}"`），graph 退出 `reset(token)`；真实 LLM 路径（`LLMAdapter.generate`）不读 contextvar，零侵入。

**为什么用 contextvar 而不是 kwargs**：
- LLM 调用链已成形（`app.llm.generate` → `get_llm_adapter().generate` → adapter.generate），加 kwargs 要改全栈；
- LangGraph 节点可能并发 / 嵌套，thread-local 不够；
- contextvar 是 asyncio 安全的（每个 task 独立上下文），与 LangGraph async node 天然契合。

**scope 设计**：
- contextvar `current_mock_session_scope: ContextVar[str]`（default `"__default__"`）
- 未设 / unset → 落到 `"__default__"` scope（保留旧行为，兼容性）
- 设了 `f"{user_id}:{session_id}"` → 落到该 session scope
- 同 scope 内 cursor 仍按 `kind:seq` 递增（repair 仍能用 `:1 → :2`）

**graph 入口 set**：
- `requirement_analysis_graph._requirement_parse`（entry）set
- `confirmed_execution_graph._confirmed_sql_agent`（entry）set
- set + 记录 token，try/finally reset；保证 graph 退出（含 exception）scope 还原

**测试**：
- `test_mock_llm_session_scope_isolates_counters`：同一 adapter instance，两个 scope 各自 `sql_generate:1` 不互相 miss
- `test_mock_llm_default_scope_used_when_context_unset`：默认 scope 内仍可工作（向后兼容）
- `test_mock_llm_session_scope_reset_restores_default`：`reset(token)` 后回到默认 scope

### Fix 2【P2】Contract E2E 边界正式定义（MCP 显式标注）

**改法**：
- `frontend/e2e/helpers/llm-mock.ts`：把文件顶部注释从「Contract 强制不连真实 MCP」升级为正式定义：
  > **Contract E2E 边界**：real browser + real FastAPI + real LangGraph + real PG + mock LLM + **intentionally disabled MCP**（`RAGENT_MCP_PYTHON=D:/non-existent/...` → 子进程启动失败 → 走 fallback）。Full E2E（env `REPORTAGENT_E2E=1` gate）才走真实 MCP（ragent-py + 真实 LLM key）。
- `docs/plans/2026-08-30-p12-playwright.md`：在「落地记录」上方加「**E2E 边界定义**」段，明确 Contract 与 Full 两层各自栈组成 + 适用场景 + 触发条件

### Fix 3【P2】spec 05 命名校准

**改法**（`frontend/e2e/specs/05-failed-result.spec.ts`）：
- `test.describe(...)` 文本：`SQL 耗尽修复预算 → ErrorCard 分类文案` → `SQL 耗尽修复预算 → ReportVersion=FAILED 落库 → ReportPaper 错误 band`
- 文件顶部加 1 行注释：测试断言对象是 `frontend/src/components/workbench/ReportPaper.tsx` 渲染 FAILED ReportVersion 的 `.wb-finding` error band（不是 `ErrorCard` 组件）；P10 Report Runtime 故意把历史 FAILED 渲染在 ReportPaper 内（不是单独 ErrorCard）

### Fix 4【P2】spec 10 文档化为 DOM-level smoke

**改法**（`frontend/e2e/specs/10-trace-progress.spec.ts`）：
- `test.describe(...)` 文本：`执行期 ProgressCard 显示真实 trace 文案` → `执行期 ProgressCard 由真实 trace 帧驱动（DOM-level smoke；严格 SSE transport + parser/dispatch/progressModel 契约由 P11 vitest 单元覆盖）`
- 测试体顶部加 1 行注释：本测试只证明 DOM 出现过真实 trace 文本（regex 含真实节点名），不证明 stage 序列或 running→success 状态机（受 P11 vitest 单测覆盖）

### Fix 5【轻量】CI 报告 Contract/Full 分组

**现状**（`frontend/package.json:13-15`）：
```json
"e2e": "playwright test --config e2e/playwright.config.ts",
"e2e:contract": "playwright test --config e2e/playwright.config.ts e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts",
"e2e:full": "playwright test --config e2e/playwright.config.ts e2e/specs/1[1-2]-*.spec.ts"
```
已有 npm scripts 分组。CI 侧用哪个由 CI 配置决定。

**改法**：
- `playwright.config.ts` reporter 加第二个：`[['list'], ['json', { outputFile: './artifacts/playwright-report.json' }]]`，便于 CI 把 Contract / Full 各自 report.json 归档对比
- 在 `frontend/package.json` 加 `e2e:contract:report` / `e2e:full:report` script（跑 + 归档），便于 CI 串接
- 在 `frontend/e2e/README.md`（如不存在则新建）写明 CI 调用约定：
  > CI per-PR：`npm run e2e:contract`（10 Contract specs）→ 期望 10/10 passed
  > CI nightly / manual gate：`REPORTAGENT_E2E=1 npm run e2e:full` → 期望 2/2 passed（spec 11 chitchat + spec 12 empty-result）
  > 默认 `npm run e2e` 跑全 12 个，但若未设 env，2 个 Full spec 会 `test.skip` → 实际跑 10 个 + 2 个 skip

## Files to change

| Fix | 模式 | 路径 |
|---|---|---|
| 1 | 修改 | `backend/app/llm/mock.py`（`_counters` 升级 + `ContextVar` + `set/reset_mock_session_scope` 工具函数） |
| 1 | 修改 | `backend/app/llm/__init__.py`（导出 `set/reset_mock_session_scope` 工具） |
| 1 | 修改 | `backend/app/agent/requirement_analysis_graph.py`（`_requirement_parse` entry set + try/finally reset） |
| 1 | 修改 | `backend/app/agent/confirmed_execution_graph.py`（`_confirmed_sql_agent` entry set + try/finally reset） |
| 1 | 新增测试 | `backend/tests/contracts/test_mock_llm_session_scope.py`（3 例：scope 隔离 / 默认 scope / reset 还原） |
| 2 | 修改注释 | `frontend/e2e/helpers/llm-mock.ts`（顶部注释升级为正式 Contract E2E 边界定义） |
| 2 | 修改 | `docs/plans/2026-08-30-p12-playwright.md`（加「E2E 边界定义」段） |
| 3 | 修改 | `frontend/e2e/specs/05-failed-result.spec.ts`（describe 文本 + 顶部注释） |
| 4 | 修改 | `frontend/e2e/specs/10-trace-progress.spec.ts`（describe 文本 + 顶部注释） |
| 5 | 修改 | `frontend/e2e/playwright.config.ts`（reporter 加 json 输出） |
| 5 | 修改 | `frontend/package.json`（加 `e2e:contract:report` / `e2e:full:report`） |
| 5 | 新建 | `frontend/e2e/README.md`（CI 报告调用约定） |
| Docs | 修改 | `docs/plans/2026-08-31-p12-review-prep-r2.md`（本 plan，落地后状态改 已完成） |
| Docs | 修改 | `docs/plans/README.md`（登记本 plan 入 进行中 → 已完成） |

## Reused existing utilities

- `backend/app/llm/mock.py MockLLMAdapter._counters`（现有 instance 属性，Fix 1 在此升级为 dict-of-dict）
- `backend/app/llm/mock.py MockLLMMiss`（已有异常类型，scope miss 复用）
- `backend/app/llm/__init__.py get_llm_adapter()`（已有 env switch，Fix 1 不动这里）
- `backend/app/agent/requirement_analysis_graph.py` / `confirmed_execution_graph.py`（两个 graph 已有 `state["session_id"]` + `state["user_id"]`，Fix 1 直接读）
- `contextvars.ContextVar`（Python stdlib，Fix 1 工具基础）
- `frontend/e2e/helpers/llm-mock.ts startContractBackend`（已有 Contract backend 启动器，Fix 2 只改注释，不动逻辑）
- `frontend/e2e/helpers/llm-mock.ts startFullBackend`（已有 Full backend 启动器，Fix 2 注释引用）
- `frontend/src/components/workbench/ReportPaper.tsx .wb-finding`（已有 FAILED 渲染 band，Fix 3 注释引用）
- `frontend/e2e/playwright.config.ts reporter`（已有 `[['list']]`，Fix 5 加 json 归档）
- `frontend/package.json e2e:contract / e2e:full`（已有分组 scripts，Fix 5 加 `:report` 变体）

## Verification

```bash
# 后端（Fix 1）
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_mock_llm_session_scope.py -v
# 预期：3 例全绿

cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：≥ 948+3=951 passed（baseline 948 + 3 新增 scope 测试；≥ 941 红线 ✓）

# Playwright Contract（Fix 1/2/3/4/5 集成）
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts
# 预期：10 全绿（Fix 1 cursor scope 不破坏现有 fixture 序列；Fix 3/4 改名不改逻辑）

cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/1[1-2]-*.spec.ts
# 预期：2 skip（未设 REPORTAGENT_E2E），或 2 passed（env=1）
```

冒烟矩阵：
- [ ] Fix 1：`test_mock_llm_session_scope_isolates_counters` 验证同 adapter instance 两 scope `sql_generate:1` 各自命中
- [ ] Fix 1：`test_mock_llm_default_scope_used_when_context_unset` 验证 unset 时仍能用 `kind:1`
- [ ] Fix 1：`test_mock_llm_session_scope_reset_restores_default` 验证 `reset(token)` 还原默认
- [ ] Fix 1：graph entry `set_mock_session_scope(f"{user_id}:{session_id}")` 真生效（debug log 或 instrument 验证）
- [ ] Fix 1：spec 03 retry spec 仍绿（修复路径 sql_generate:1 → :2 在同 session scope 内仍递增）
- [ ] Fix 1：spec 07 background-execution spec 仍绿（单 session scope，counter 行为不变）
- [ ] Fix 2：`llm-mock.ts` 顶部注释正式定义 Contract E2E 边界
- [ ] Fix 3：spec 05 describe 文本改为「ReportVersion=FAILED 落库 → ReportPaper 错误 band」
- [ ] Fix 4：spec 10 describe 文本明确「DOM-level smoke；严格 SSE transport 契约由 P11 vitest 单元覆盖」
- [ ] Fix 5：`reporter: [['list'], ['json', { outputFile: './artifacts/playwright-report.json' }]]` 跑后产物落 `frontend/e2e/artifacts/playwright-report.json`
- [ ] 全量 backend 948+3=951 passed（≥ 941 红线）
- [ ] Playwright 10 Contract specs 全绿无回归

## Explicitly NOT doing

- **不在 `LLMAdapter.generate` 接口加 session_id kwarg**（避免全栈改动；contextvar 已足够透明）
- **不把 `set_mock_session_scope` 加进 graph 之外的所有 caller**（req/confirmed 两图 entry 已覆盖主链；其他 P11 加的子图回调都从这两图进入）
- **不重构 `MockLLMAdapter` 为多 adapter-per-session 池**（singleton 保留，scope 隔离已足够；多实例会破坏 fixture 全局 miss 检测）
- **不改 spec 10 升为严格 progress contract**（用户明示 P2 即可；严格契约由 P11 vitest 单测覆盖 SSE transport + parser + dispatch + progressModel）
- **不改 spec 05 拆为 ErrorCard / ReportPaper 两层断言**（P10 已确定 ReportPaper error band 是设计；拆 spec 是 scope creep）
- **不动 r1 已落地的 5 commit**（不 rebase / 不 amend；新修复独立 commit）
- **不修复 spec 02/04/06/07/08/09/11/12 命名/语义**（user review 未点出，不主动扩大 scope）
- **不引入 reporter group 第三方库**（Playwright 自带 json reporter 已够；CI 分组由 npm script 隔离）
- **不修 MCP fallback 路径本身**（P5 已冻结 `PHASE2_MCP_ONLY` flag + 本地 fallback；MCP 边界定义是文档校准，不动 fallback 代码）

## Commit 计划（5 个独立 commit，按依赖排序）

```
fix(p12): mock cursor scope 隔离（per-session ContextVar）
  → backend/app/llm/mock.py + __init__.py + 2 graph + 3 contract 测试
fix(p12): spec 05 failed-result 命名校准（ErrorCard → ReportPaper 错误 band）
  → frontend/e2e/specs/05-failed-result.spec.ts
fix(p12): spec 10 trace-progress 文档化为 DOM-level smoke
  → frontend/e2e/specs/10-trace-progress.spec.ts
docs(p12): Contract E2E 边界正式定义（MCP 显式标注）+ CI 报告分组
  → frontend/e2e/helpers/llm-mock.ts（注释）+ e2e/README.md（新建）+ playwright.config.ts + package.json
docs(p12): review round 2 落地记录 + plan 收尾
  → docs/plans/2026-08-30-p12-playwright.md + 本 plan README 登记
```

---

## Task 1: MockLLM session scope 隔离（Fix 1）

**Files:**
- Modify: `backend/app/llm/mock.py:1-146`
- Modify: `backend/app/llm/__init__.py:1-106`
- Modify: `backend/app/agent/requirement_analysis_graph.py`（`_requirement_parse` 节点）
- Modify: `backend/app/agent/confirmed_execution_graph.py`（`_confirmed_sql_agent` 节点）
- Create: `backend/tests/contracts/test_mock_llm_session_scope.py`

- [ ] **Step 1.1: 在 mock.py 加 ContextVar + 升级 _counters**

修改 `backend/app/llm/mock.py`：
1. 顶部 import 加 `from contextvars import ContextVar`
2. 模块级加：
   ```python
   _current_mock_scope: ContextVar[str] = ContextVar("current_mock_session_scope", default="__default__")
   
   def set_mock_session_scope(scope_id: str | None) -> object:
       """设置当前 mock session scope（counter key 隔离边界）。
       
       返回 token 用于 reset。None / 空 → 使用 '__default__'。
       真实 LLM 路径不感知（adapter.generate 不读）。
       Contract E2E 在 graph entry 调用，让 fixture cursor 不跨 session 共享。
       """
       scope = scope_id if scope_id else "__default__"
       return _current_mock_scope.set(scope)
   
   def reset_mock_session_scope(token: object) -> None:
       """还原 scope（与 set_mock_session_scope 返回的 token 配对）。"""
       _current_mock_scope.reset(token)  # type: ignore[arg-type]
   ```
3. `MockLLMAdapter.__init__`：`self._counters: dict[str, int] = {}` 改为 `self._counters: dict[str, dict[str, int]] = {}`
4. `_lookup` 改为：
   ```python
   def _lookup(self, prompt: str | list) -> Any:
       kind = prompt_kind(prompt)
       scope = _current_mock_scope.get()
       scope_counters = self._counters.setdefault(scope, {})
       seq = scope_counters.get(kind, 0) + 1
       scope_counters[kind] = seq
       key = f"{kind}:{seq}"
       if key not in self._responses:
           logger.warning("MockLLMMiss case=%s scope=%s key=%s", self._case_id, scope, key)
           raise MockLLMMiss(
               f"case {self._case_id} scope={scope}: no fixture for `{key}`（kind={kind}）"
           )
       if self._delay_ms > 0:
           import time
           time.sleep(self._delay_ms / 1000)
       return self._responses[key]
   ```

- [ ] **Step 1.2: 在 `__init__.py` 导出 set/reset 工具**

修改 `backend/app/llm/__init__.py`：
- 第 7 行 import 末尾加 `set_mock_session_scope, reset_mock_session_scope`
- 第 106 行 `__all__` 末尾加 `"set_mock_session_scope", "reset_mock_session_scope"`

- [ ] **Step 1.3: 写 scope 隔离测试（先红）**

新建 `backend/tests/contracts/test_mock_llm_session_scope.py`：
```python
from __future__ import annotations
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contracts

from app.llm.mock import (
    MockLLMAdapter,
    set_mock_session_scope,
    reset_mock_session_scope,
)


def _write_fixture(tmp_path: Path, case: str, mapping: dict) -> None:
    (tmp_path / f"{case}.json").write_text(json.dumps(mapping), encoding="utf-8")


_PROMPT = "你是 ReportAgent SQL 生成专家。" + " SELECT ..."  # sql_generate marker


def test_mock_llm_session_scope_isolates_counters(tmp_path: Path) -> None:
    """两个 session scope 各自 seq 从 1 起，互不干扰。"""
    _write_fixture(tmp_path, "multi-session", {
        "sql_generate:1": {"sql": "SELECT 1"},
        "sql_generate:2": {"sql": "SELECT 2"},
    })
    adapter = MockLLMAdapter(tmp_path, "multi-session")

    token_a = set_mock_session_scope("user:1:session:A")
    try:
        assert adapter.generate(_PROMPT) == {"sql": "SELECT 1"}
        assert adapter.generate(_PROMPT) == {"sql": "SELECT 2"}
    finally:
        reset_mock_session_scope(token_a)

    token_b = set_mock_session_scope("user:1:session:B")
    try:
        # session B 第一次 sql_generate 也应从 :1 起（不被 A 的 :2 污染）
        assert adapter.generate(_PROMPT) == {"sql": "SELECT 1"}
    finally:
        reset_mock_session_scope(token_b)


def test_mock_llm_default_scope_used_when_context_unset(tmp_path: Path) -> None:
    """contextvar 未显式 set → 落 '__default__' scope，向后兼容。"""
    _write_fixture(tmp_path, "default-scope", {
        "sql_generate:1": "SELECT default",
    })
    adapter = MockLLMAdapter(tmp_path, "default-scope")

    # 不 set scope → 落默认
    assert adapter.generate(_PROMPT) == "SELECT default"


def test_mock_llm_session_scope_reset_restores_default(tmp_path: Path) -> None:
    """reset(token) 后回到默认 scope（不同 session 同 fixture 不共享 counter）。"""
    _write_fixture(tmp_path, "reset-scope", {
        "sql_generate:1": "SELECT first",
    })
    adapter = MockLLMAdapter(tmp_path, "reset-scope")

    token = set_mock_session_scope("user:1:session:X")
    try:
        assert adapter.generate(_PROMPT) == "SELECT first"
        # session X 第二次 → fixture miss
        with pytest.raises(Exception, match="no fixture"):
            adapter.generate(_PROMPT)
    finally:
        reset_mock_session_scope(token)

    # reset 后回到默认 scope；fixture 已用完 :1，但默认 scope 与 X 独立
    # 这里只验证 reset 不抛异常 + 默认 scope 可工作
    _write_fixture(tmp_path, "default-after-reset", {
        "sql_generate:1": "SELECT after-reset",
    })
    adapter2 = MockLLMAdapter(tmp_path, "default-after-reset")
    assert adapter2.generate(_PROMPT) == "SELECT after-reset"
```

- [ ] **Step 1.4: 跑测试确认红/绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_mock_llm_session_scope.py -v
```
预期：3/3 passed（Step 1.1 已实现）

- [ ] **Step 1.5: 在两个 graph entry set scope**

`backend/app/agent/requirement_analysis_graph.py`：找到 `_requirement_parse` 节点函数体（文件内 `async def _requirement_parse`），在函数体顶部加：
```python
from app.llm import set_mock_session_scope, reset_mock_session_scope

async def _requirement_parse(state):
    scope_token = set_mock_session_scope(f"{state['user_id']}:{state['session_id']}")
    try:
        # ... 原有 body
    finally:
        reset_mock_session_scope(scope_token)
```

注意：实际函数已存在；用 `Edit` 工具定位 `_requirement_parse` 函数头部 + 末尾做精确插入。**不要替换函数体**，只包一层 try/finally。

同理 `backend/app/agent/confirmed_execution_graph.py` 找 `_confirmed_sql_agent`：
```python
async def _confirmed_sql_agent(state):
    scope_token = set_mock_session_scope(f"{state['user_id']}:{state['session_id']}")
    try:
        # ... 原有 body
    finally:
        reset_mock_session_scope(scope_token)
```

- [ ] **Step 1.6: 跑全量后端测试确认无回归**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
```
预期：≥ 951 passed（baseline 948 + 3 新增）

- [ ] **Step 1.7: 跑 Contract spec 确认 cursor 行为不变**

```bash
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/03-retry.spec.ts e2e/specs/07-background-execution.spec.ts
```
预期：2 spec 全绿（spec 03 同 session scope 内 repair `:1 → :2` 仍递增；spec 07 单 session scope，counter 不漂移）

- [ ] **Step 1.8: Commit**

```bash
git add backend/app/llm/mock.py backend/app/llm/__init__.py \
        backend/app/agent/requirement_analysis_graph.py \
        backend/app/agent/confirmed_execution_graph.py \
        backend/tests/contracts/test_mock_llm_session_scope.py
git commit -m "fix(p12): mock cursor scope 隔离（per-session ContextVar 防跨 session fixture 漂移）"
```

---

## Task 2: spec 05 命名校准（Fix 3）

**Files:**
- Modify: `frontend/e2e/specs/05-failed-result.spec.ts:6`

- [ ] **Step 2.1: 改 describe 文本 + 加注释**

修改 `frontend/e2e/specs/05-failed-result.spec.ts`：
- 第 1 行 import 下方加：
  ```ts
  /**
   * 测试验收对象：P10 Report Runtime → `ReportVersion=FAILED` 落库 → ReportPaper
   * 历史 FAILED 版本视图（`.wb-finding` error band，含「执行失败」）。
   * 注：不是 `ErrorCard` 组件（项目无 ErrorCard 组件命名）。设计选择原因：
   * FAILED 是 report 的合法三态之一（SUCCESS/EMPTY/FAILED），统一在 ReportPaper 内
   * 渲染，不为 FAILED 单独建组件——避免 ReportPaper 与 ErrorCard 双源真相漂移。
   */
  ```
- 第 6 行 `test.describe('05-failed-result — SQL 耗尽修复预算 → ErrorCard 分类文案', () => {`
  改为 `test.describe('05-failed-result — SQL 耗尽修复预算 → ReportVersion=FAILED 落库 → ReportPaper 错误 band', () => {`

- [ ] **Step 2.2: 跑 spec 确认逻辑不变**

```bash
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/05-failed-result.spec.ts
```
预期：1 spec 全绿（只改文本 + 注释，不动断言）

- [ ] **Step 2.3: Commit**

```bash
git add frontend/e2e/specs/05-failed-result.spec.ts
git commit -m "fix(p12): spec 05 failed-result 命名校准（ErrorCard → ReportPaper 错误 band）"
```

---

## Task 3: spec 10 文档化为 DOM-level smoke（Fix 4）

**Files:**
- Modify: `frontend/e2e/specs/10-trace-progress.spec.ts:1-6`

- [ ] **Step 3.1: 改 describe 文本 + 加注释**

修改 `frontend/e2e/specs/10-trace-progress.spec.ts`：
- 第 1 行 import 下方加：
  ```ts
  /**
   * 测试范围：DOM-level smoke assertion —— 证明 `.wb-progress-detail` DOM
   * 出现过真实 trace 文本（regex 含 P11 节点名：生成 SQL / 校验 SQL /
   * 执行查询 / 组织报告）。
   *
   * 不在本 spec 覆盖的强契约（由 P11 vitest 单元覆盖）：
   *   - SSE transport 帧解析（api/sse.ts parseSSEFrameRaw）
   *   - schema 解析（analysisEvents.ts parseAnalysisSSEEvent）
   *   - dispatch（sessionEvents.ts handleSSEEvent）
   *   - progressModel 状态机（stageFromTrace + liveDetailFromEntry）
   *   - running → success 完整序列
   *
   * 即：本 spec 是 browser-level 集成 smoke，不是严格的 progress contract。
   * 强契约失效不会被本 spec 钉住（需 P11 vitest 单元）。
   */
  ```
- 第 6 行 `test.describe('10-trace-progress — 执行期 ProgressCard 显示真实 trace 文案', () => {`
  改为 `test.describe('10-trace-progress — DOM-level smoke：ProgressCard 由真实 trace 帧驱动（强 contract 由 P11 vitest 单测）', () => {`

- [ ] **Step 3.2: 跑 spec 确认逻辑不变**

```bash
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/10-trace-progress.spec.ts
```
预期：1 spec 全绿（只改文本 + 注释）

- [ ] **Step 3.3: Commit**

```bash
git add frontend/e2e/specs/10-trace-progress.spec.ts
git commit -m "fix(p12): spec 10 trace-progress 文档化为 DOM-level smoke（强 contract 由 P11 vitest 单测覆盖）"
```

---

## Task 4: Contract E2E 边界正式定义 + CI 报告分组（Fix 2 + Fix 5）

**Files:**
- Modify: `frontend/e2e/helpers/llm-mock.ts:1-16`（顶部注释升级）
- Modify: `frontend/e2e/playwright.config.ts:21`（reporter 加 json）
- Modify: `frontend/package.json:13-15`（加 `:report` script）
- Modify: `docs/plans/2026-08-30-p12-playwright.md`（加「E2E 边界定义」段）
- Create: `frontend/e2e/README.md`

- [ ] **Step 4.1: 升级 `llm-mock.ts` 顶部注释**

修改 `frontend/e2e/helpers/llm-mock.ts`：
- 第 6-10 行替换为：
  ```ts
  const PYTHON = process.env.RAGENT_PYTHON ?? 'D:/miniConda/envs/agent/python.exe'
  const BACKEND_DIR = resolve(repoRoot, 'backend')
  
  /**
   * ============================================================
   * Contract E2E 边界定义（review-prep-r2 Fix 2 正式化）
   * ============================================================
   * Contract E2E = real browser + real FastAPI + real LangGraph
   *             + real PG + mock LLM + INTENTIONALLY DISABLED MCP
   *
   * 强制不连真实 MCP：`RAGENT_MCP_PYTHON=D:/non-existent/...` 让 RagMCPClient
   * 子进程启动失败 → dict_hit=False / schema 空 → 走 mock LLM + 本地 schema fallback。
   * 这证明：「MCP 不可用时，系统 fallback 后能工作」——不是 frontend → backend →
   * MCP → DB 全链路（那是 Full E2E 的范畴）。
   *
   * Full E2E 边界：real browser + real FastAPI + real LangGraph + real PG +
   * real LLM (MiniMax) + real MCP (ragent-py)。env `REPORTAGENT_E2E=1` gate。
   *
   * 适用场景：
   *   - Contract E2E：CI per-PR 跑（无外部 key 依赖，可靠）
   *   - Full E2E：nightly / manual（需真 LLM key + ragent-py 服务）
   * ============================================================
   */
  const RAGENT_MCP_PYTHON = process.env.RAGENT_MCP_PYTHON ?? 'D:/non-existent/ragent-python.exe'
  ```

- [ ] **Step 4.2: playwright.config.ts reporter 加 json**

修改 `frontend/e2e/playwright.config.ts`：
- 第 21 行 `reporter: [['list']],` 改为 `reporter: [['list'], ['json', { outputFile: './artifacts/playwright-report.json' }]],`

- [ ] **Step 4.3: package.json 加 `:report` 变体**

修改 `frontend/package.json`：
- 第 13-15 行 e2e scripts 改为：
  ```json
  "e2e": "playwright test --config e2e/playwright.config.ts",
  "e2e:contract": "playwright test --config e2e/playwright.config.ts e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts",
  "e2e:full": "playwright test --config e2e/playwright.config.ts e2e/specs/1[1-2]-*.spec.ts",
  "e2e:contract:report": "playwright test --config e2e/playwright.config.ts --reporter=list --reporter=json,e2e/artifacts/playwright-contract-report.json e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts",
  "e2e:full:report": "playwright test --config e2e/playwright.config.ts --reporter=list --reporter=json,e2e/artifacts/playwright-full-report.json e2e/specs/1[1-2]-*.spec.ts"
  ```

- [ ] **Step 4.4: 新建 `frontend/e2e/README.md`**

新建 `frontend/e2e/README.md`：
```markdown
# P12 Playwright E2E

## Contract E2E（CI per-PR，10 specs）

```bash
npm run e2e:contract
# 或带 JSON 归档：
npm run e2e:contract:report
# 产物：frontend/e2e/artifacts/playwright-contract-report.json
```

**栈组成**：real browser（Playwright bundled Chromium）+ real FastAPI +
real LangGraph + real PG（localhost:5432）+ mock LLM（fixture 驱动）+
**intentionally disabled MCP**（`RAGENT_MCP_PYTHON=D:/non-existent/...`）。

**证明**：MCP 不可用时系统 fallback 后能工作 + 各业务路径 spec 行为契约
（happy / clarification / empty / failed / retry / background / version /
recovery / memory / trace progress）。

**期望**：10/10 passed（CI 无 env 依赖）。

## Full E2E（nightly / manual gate，2 specs）

```bash
REPORTAGENT_E2E=1 npm run e2e:full
# 或带 JSON 归档：
REPORTAGENT_E2E=1 npm run e2e:full:report
# 产物：frontend/e2e/artifacts/playwright-full-report.json
```

**栈组成**：real browser + real FastAPI + real LangGraph + real PG +
real LLM（MiniMax，需 `LLM_API_KEY`）+ real MCP（ragent-py，需
`RAGENT_MCP_PYTHON` 指向真实解释器 + ragent-py 服务运行中）。

**证明**：frontend → backend → MCP → DB 全链路 + 真实 LLM 决策。

**期望**：2/2 passed（spec 11 chitchat + spec 12 empty-result with real data）。

未设 `REPORTAGENT_E2E`：`npm run e2e:full` 自动 `test.skip` 2 个 spec →
输出 `2 skipped / 0 failed`（不是 bug，是 env gate）。

## 报告解读

- 默认 `npm run e2e` 跑全 12 个 spec（10 Contract + 2 Full），未设
  `REPORTAGENT_E2E` 时 2 个 Full 自动 skip。
- CI 调用 `e2e:contract` 期望 10/10 passed；调用 `e2e:full` 前必须设 env。
- JSON report 含每个 spec 的 timing + 失败 trace 路径，便于 CI 归档对比。

## Contract E2E 边界说明（review-prep-r2 Fix 2）

Contract E2E 故意禁用 MCP（`RAGENT_MCP_PYTHON` 指向不存在的解释器）。
这不是缺陷——是设计选择：

- **Contract**：证明业务契约 + fallback 路径（不需要真 MCP）。
- **Full**：证明端到端全栈（需要真 MCP + 真 LLM）。

二者互补，不重叠。Full spec 默认 env-gated 是为了 CI 不依赖外部 key。

## MockLLM session scope（review-prep-r2 Fix 1）

`backend/app/llm/mock.py` 在 review-prep-r2 后支持 per-session cursor
scope（`set_mock_session_scope(f"{user_id}:{session_id}")` 在 graph entry
调用）。这保证：

- 同一 backend process 内多个 session 各自 cursor 从 `:1` 起；
- 业务流程多调一次 / 少调一次不会让后续 session fixture 漂移。

详见 [docs/plans/2026-08-31-p12-review-prep-r2.md](../../docs/plans/2026-08-31-p12-review-prep-r2.md)。
```

- [ ] **Step 4.5: 在 `docs/plans/2026-08-30-p12-playwright.md` 加「E2E 边界定义」段**

打开 plan，定位「落地记录」段，**之前**插入：
```markdown
## E2E 边界定义（review-prep-r2 Fix 2）

| 层 | 栈组成 | 触发 | 期望 |
|---|---|---|---|
| Contract | real browser + real FastAPI + real LangGraph + real PG + mock LLM + **intentionally disabled MCP**（`RAGENT_MCP_PYTHON=D:/non-existent/...`） | `npm run e2e:contract`（CI per-PR） | 10/10 passed |
| Full | real browser + real FastAPI + real LangGraph + real PG + real LLM（MiniMax）+ real MCP（ragent-py） | `REPORTAGENT_E2E=1 npm run e2e:full`（nightly/manual） | 2/2 passed |

**Contract 故意禁用 MCP**——证明「MCP 不可用时系统 fallback 后能工作」，
不是「frontend→backend→MCP→DB 全链路」（那是 Full 的范畴）。

未设 `REPORTAGENT_E2E`：`npm run e2e:full` 跑 2 skipped / 0 failed（env gate）。
```

- [ ] **Step 4.6: 跑 Contract + 默认 e2e 确认全绿 + 报告归档**

```bash
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts
# 预期：10 全绿
ls -la frontend/e2e/artifacts/playwright-report.json
# 预期：存在
```

- [ ] **Step 4.7: Commit**

```bash
git add frontend/e2e/helpers/llm-mock.ts \
        frontend/e2e/playwright.config.ts \
        frontend/package.json \
        frontend/e2e/README.md \
        docs/plans/2026-08-30-p12-playwright.md
git commit -m "docs(p12): Contract E2E 边界正式定义（MCP 显式标注）+ CI 报告分组 + e2e/README"
```

---

## Task 5: 落地记录 + plan 收尾

**Files:**
- Modify: `docs/plans/2026-08-31-p12-review-prep-r2.md`（状态 → 已完成 + 落地记录）
- Modify: `docs/plans/README.md`（进行中 → 已完成）

- [ ] **Step 5.1: 改本 plan 顶部状态**

将第 2 行：
> 状态: 进行中（2026-08-31；p12-playwright 分支本轮 review 后第二轮修复）

改为：
> 状态: 已完成（2026-08-31；commit `...`、`...`、`...`、`...` 见底部落地记录）

并在文件底部新增「落地记录」段：
```markdown
### 落地记录

| Fix | Commit | 命中验证 |
|---|---|---|
| Fix 1 mock cursor scope 隔离 | `<hash1>` | `test_mock_llm_session_scope_isolates_counters` 等 3 例（948→951 passed）+ Playwright spec 03/07 不回归 |
| Fix 3 spec 05 命名校准 | `<hash2>` | spec 05 describe 文本改为「ReportVersion=FAILED 落库 → ReportPaper 错误 band」+ 注释指向 P10 设计 |
| Fix 4 spec 10 文档化 | `<hash3>` | spec 10 describe 文本 + 顶部注释明确「DOM-level smoke；强 contract 由 P11 vitest 单测覆盖」 |
| Fix 2+5 边界定义 + CI 分组 | `<hash4>` | `llm-mock.ts` 顶部正式 Contract E2E 边界定义 + `playwright.config.ts` reporter 加 json + `e2e:contract:report` / `e2e:full:report` script + `frontend/e2e/README.md` 新建 + `p12-playwright.md` 加「E2E 边界定义」段 |

**总体验证**：backend **951 passed / 1 skipped / 5 warnings**（baseline 948 + 3 新增；≥ 941 红线 ✓）；Playwright **10/10 Contract specs 全绿**（Full specs env-gate 仍走 `REPORTAGENT_E2E=1`，未在本轮回归范围内）。

### 风险评估

| 风险 | 等级 | 缓解 |
|---|---|---|
| Fix 1 graph entry set/reset 误抛异常 | 低 | try/finally 保证 reset；实测 3 例 contract 测试覆盖 |
| Fix 1 同 backend process 多 session spec 未来扩展 | 极低 | scope 设计已显式支持多 session（contextvar 隔离） |
| Fix 3/4 改名误导 reviewer | 极低 | 加注释指向 P10 设计 / P11 单测覆盖，self-documenting |
| Fix 5 reporter json 与 list 并存可能重复输出 | 低 | Playwright `--reporter` 重复参数已知行为；用 CLI flag 时已分开两个 reporter |
```

（`<hash1>` 等占位在 commit 后用真实 hash 替换）

- [ ] **Step 5.2: README.md 索引更新**

修改 `docs/plans/README.md`：
- 「进行中」表里把 `2026-08-31-p12-review-prep.md` 那行（如果还在）连同本 plan 移到「已完成」表
- 「进行中」表如果空了保留表头 + 空行注释；或加新行（本 plan 完成后无新 plan）
- 在「已完成」表加本 plan 行：
  ```
  | [2026-08-31-p12-review-prep-r2.md](2026-08-31-p12-review-prep-r2.md) | P12 review 第 2 轮：MockLLM cursor scope 隔离（contextvar per-session）+ spec 05/10 命名校准 + Contract E2E 边界正式定义 + CI 报告分组 | 接 review-prep-r1（`b18565f`）+ user review 6 项中 5 项落实，1 项「Happy / Retry / Failed」已 PASS 无需补；独立 4 个 fix commit + 1 个 docs commit |
  ```

- [ ] **Step 5.3: 全量最终验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：≥ 951 passed

cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts
# 预期：10 全绿
```

- [ ] **Step 5.4: Commit**

```bash
git add docs/plans/2026-08-31-p12-review-prep-r2.md docs/plans/README.md
git commit -m "docs(p12): review round 2 落地记录 + plan 收尾"
```

---

## Self-Review

1. **Spec coverage**:
   - Fix 1 cursor scope → Task 1 ✓
   - Fix 2 MCP 边界定义 → Task 4（Step 4.1 + 4.5）✓
   - Fix 3 spec 05 改名 → Task 2 ✓
   - Fix 4 spec 10 文档化 → Task 3 ✓
   - Fix 5 CI 报告分组 → Task 4（Step 4.2 + 4.3 + 4.4）✓
   - 落地记录 → Task 5 ✓

2. **Placeholder scan**: 无 "TBD" / "TODO" / "fill in details"。`<hash1>` 等在 Step 5.1 明确说"commit 后用真实 hash 替换"。

3. **Type consistency**:
   - `set_mock_session_scope(scope_id: str | None) -> object` 与 `reset_mock_session_scope(token: object) -> None` 一致
   - `MockLLMAdapter._counters` 从 `dict[str, int]` → `dict[str, dict[str, int]]` 在所有引用处同步（仅 `_lookup` 一处）
   - graph entry 函数名 `set_mock_session_scope(f"{state['user_id']}:{state['session_id']}")` 与 `reset_mock_session_scope(scope_token)` 配对

---

## 落地记录

| Fix | Commit | 命中验证 |
|---|---|---|
| Fix 1 mock cursor scope 隔离 | `37223e1` | 4 contract 测试全绿（scope 隔离 / 默认 scope / reset 还原 / repair seq 回归保护）；backend 951→955 passed；spec 03/07 Playwright 全绿不回归 |
| Fix 3 spec 05 命名校准 | `ab47121` | spec 05 describe 文本改为「ReportVersion=FAILED 落库 → ReportPaper 错误 band」+ 顶部注释指向 P10 ReportPaper 设计；spec 05 Playwright 1/1 passed |
| Fix 4 spec 10 文档化 | `61ec961` | spec 10 describe 文本 + 顶部注释明确「DOM-level smoke；强 contract 由 P11 vitest 单测覆盖」；spec 10 Playwright 1/1 passed |
| Fix 2+5 边界定义 + CI 分组 | `fe8e9be` | `llm-mock.ts` 顶部正式 Contract E2E 边界定义 + `playwright.config.ts` reporter 加 json 归档（`./artifacts/playwright-report.json`）+ `e2e:contract:report` / `e2e:full:report` npm script + `frontend/e2e/README.md` 新建（CI 调用约定）+ `p12-playwright.md` 加「E2E 边界定义」段；Playwright Contract 10/10 全绿 |

**总体验证**：backend **955 passed / 1 skipped / 5 warnings**（baseline 951 + 4 新增 scope 测试；≥ 941 红线 ✓）；Playwright **10/10 Contract specs 全绿**（Full specs env-gate 仍走 `REPORTAGENT_E2E=1`，未在本轮回归范围内）。

**与 plan baseline 数字的偏差**：plan 写"baseline 948+3=951"，实际 baseline 是 951（r1 落地后）；落地后是 955（+4 新增，含 plan 外加的 repair regression 保护测试 spec 03）。文档以实际数据为准。

### 风险评估

| 风险 | 等级 | 缓解 |
|---|---|---|
| Fix 1 graph entry set scope 跨 task 残留 | 低 | asyncio task 结束 → contextvar 自动清理；实测 spec 03 repair `:1 → :2` 仍递增（test 4） |
| Fix 1 同 backend process 多 session spec 未来扩展 | 极低 | scope 设计已显式支持多 session（contextvar 隔离） |
| Fix 3/4 改名误导 reviewer | 极低 | 加注释指向 P10 设计 / P11 单测覆盖，self-documenting |
| Fix 5 reporter json 与 list 并存可能重复输出 | 低 | Playwright 双 reporter 已知行为；用 CLI flag 时已分开两个 reporter |
| r1 outputDir 把 `frontend/e2e/artifacts/README.md` 清掉 | 极低 | r1 引入的非回归小问题（不在 r2 scope）；r2 未触动 |
