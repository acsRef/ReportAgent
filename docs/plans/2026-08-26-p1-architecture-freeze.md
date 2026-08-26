# P1 Architecture Freeze 实施 plan

> 状态: 进行中
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md)（§二·二 目标目录 / §十七 Legacy Policy / §十八 P1 验收清单）
> 前置: P0 Baseline Lock 已落地（434d4a5，16 pass / 0 fail / 4 skip）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 P1 三件事——五份架构契约文档、legacy 物理归置 + import 断言钉死、CLAUDE.md 宪法版重写；附带修掉 P0 发现的 e2e 陈旧断言。

**Architecture:** 先归置代码（让文档描述的是真实状态）、再用断言测试钉住边界、然后写文档与宪法版 CLAUDE.md（描述冻结后的形态并标注现状差距防反向漂移）。legacy 只归置不删除（删除是 P15）。

**Tech Stack:** Python 3.11 (`D:/miniConda/envs/agent/python.exe`) + pytest；React 19 + TypeScript + vitest + oxlint；git mv 保历史。

**执行纪律（评审定稿）：** Task 0→6 严格顺序不调换；每 Task 一个 commit 不合并；Task 4 五份文档严格「素材直采伞形 plan、不扩写」；CLAUDE.md 只写 invariant / forbidden patterns / canonical flow / ownership / change discipline，实现细节一律指向 docs/architecture/*，防止膨胀成第二份 spec；Task 0 只改那一处断言，不顺手扩大。

---

## Context

伞形 plan（refactor-master-freeze）冻结了 15 个 Phase 的重构路线。P0 已完成基线锁定，本 plan 是 **P1 Architecture Freeze + Legacy Policy** 的实施 plan，对应伞形 plan 的以下要求：

- §二·二：目标目录冻结，`backend/app/legacy/` 顶层一份，新代码 MUST NOT import；
- §十七：legacy 归置（parent_graph legacy mode 等）标 deprecated、import 断言钉住、Phase 15 才删；
- §十八 P1 验收清单：Agent 职责明确 ✅ Workflow 职责明确 ✅ State 字段明确 ✅ 无新代码 import legacy ✅ 架构图完成 ✅ CLAUDE.md 宪法版落地 ✅；
- §十八 P1 说明：五份文档锁定后立即重写 CLAUDE.md（宪法版），LLM Policy 只写「统一 reasoning model、provider 无关」。

触发本次实施的原始诉求（用户 2026-08-26）：

> 开工 P1 Architecture Freeze：五份架构文档 + CLAUDE.md 宪法版重写 + legacy 归置，先出 P1 实施 plan 给我过目再动手。另外 e2e 陈旧断言小修（test_full_flow.py:177 locked → complete）顺手可修。

当前痛点：P2~P14 施工期间每个 Claude Code 会话以 CLAUDE.md 为第一上下文，若不先冻结宪法，施工全程读到的都是旧架构描述，必然指导漂移；同时 legacy 链路与现役链路物理混杂，没有边界断言，「新链路 + 旧链路 + 新功能又偷偷接旧代码」的三岔路随时复发。

---

## 设计

### 决策 1：legacy 判定标准——「仅被旧链路引用」，不看名字

**名字带 legacy 的不一定是 legacy，被旧链路独占引用的才是。** 全量追溯 import 关系后的三分清单：

**A. 真 legacy（物理移入 legacy/）**：

| 资产 | 证据 |
|---|---|
| `backend/app/agent/parent_graph.py` | 仅被 `app/agent/__init__.py`（re-export）与 `main.py`（`mode=legacy` 分支）引用；整张图（security_guard→classify→data_agent→sql_agent→evaluate→report_agent/clarify/dashboard）只服务旧 2-stage interrupt 流 |
| `backend/app/db.py` | DuckDB 兼容路径；仅 `main.py` lifespan 启停时引用，运行期零消费（分析 SQL 走 `sql_tools` 的 psycopg2 `ANALYSIS_DSN`，应用持久化走 asyncpg pool）——CLAUDE.md 已明说是 legacy path |
| `frontend/src/pages/ChatPage.tsx` | 仅 `/legacy/chat` 路由使用 |
| `frontend/src/pages/TemplateCenter.tsx` | 仅 `/legacy/templates` 路由使用 |
| `frontend/src/pages/HistoryPage.tsx` | 仅 `/history` 路由使用（App.tsx 注释「kept for Phase 8 evaluation」） |
| `frontend/src/pages/StandaloneReportPage.tsx` | **孤儿组件**：无任何路由/组件引用 |
| `frontend/src/pages/views/{ChatView,RunningView,ReportView}.tsx` | 仅被 ChatPage 引用 |
| `frontend/src/stores/session.ts` | 仅被上述 4 个 legacy 页面引用（WorkbenchPage 用自己的 analysisReducer + sessionsClient，不经此 store） |
| `frontend/src/api/chat.ts`（chatStream） | 仅被 session store 引用；Workbench 走 `analysisClient.openChat` |
| `frontend/src/api/legacyAdapter.ts` | 仅其自身测试引用 |

**B. 名字像但不是 legacy（原地不动，写进文档澄清）**：

- `app/agent/data_graph.py` — requirement_analysis_graph 与 confirmed_execution_graph 共用的 schema 发现图；
- `app/agent/intent.py` / `requirement_parser.py` / `requirement_options.py` — requirement_analysis_graph 在用；
- `app/agent/sql_graph.py` 的 `_intent_analyze` 入口节点 — confirmed 构图时绕过该入口但物理共存于同一文件，P15 随整文件处置；
- `frontend/src/api/analysisClient.ts` 的 `mode: 'legacy'` 联合成员 — 后端 mode 还在，契约不动；
- `frontend/src/stores/templateStore.ts` 的 legacy key — 是 localStorage 迁移逻辑，不是代码 legacy；
- `backend/tests/test_security_hardening.py` 的 `test_all_legacy_*` — 指「旧注入样本集」，测试名不改。

**C. 无法物理移动的 legacy 代码（LEGACY BRIDGE 锚点机制，见决策 2）**：`main.py` 的 `_chat_legacy` / `_format_event` / `_build_response` / `_legacy_lock` / `_VALID_CHOSEN_TOOLS`。`/api/v1/chat` 是 v2+legacy 共享入口（A-2 owner 校验在分发前），FastAPI 路由 + auth deps + EventSourceResponse 与 HTTP 层深度耦合，硬搬会造出 `legacy/api` 这个冻结结构里不存在的子目录（§二·二只冻结 agents/tools/adapters 三子目录）。

### 决策 2：LEGACY BRIDGE 锚点——显式豁免区，禁止扩大

`main.py` 中 legacy 分支所需的跨模块引用（`from app.legacy.agents.parent_graph import ...`；`db.py` 摘除后预计无第二条件）集中到一个显式 `BEGIN`/`END` 标记界定的锚点区块（形状如下）：

```python
# ===========================================================================
# LEGACY BRIDGE BEGIN — mode=legacy 专属引用。禁止在此区块外 import app.legacy.*，
# 禁止向此区块新增条目（Phase 15 整体删除）。见 docs/architecture/* 与
# docs/plans/2026-08-26-p1-architecture-freeze.md 决策 2。
from app.legacy.agents.parent_graph import build_parent_graph
# LEGACY BRIDGE END
# ===========================================================================
```

区块规则（写死，测试钉住）：`BEGIN` 与 `END` 标记行之间**只允许出现 legacy import 语句**（import 行 + 必要的行内注释），不允许其他语句——标记对构成语法上可扫描的封闭区间，测试不依赖「注释/空行到第一条非注释行为止」这类脆弱文本推断。

顺带清理：`main.py:28` import 的 `AgentState` 在文件内无消费点（历史遗留），随迁移一并删除；lifespan 中的 `get_connection()` / `close_connection()`（DuckDB 启停，运行期无人消费）直接摘除——这不是「对 legacy 的功能改动」而是**切断现役代码对 legacy 的依赖**，正是 P1 归置的定义。`db.py` 移入 legacy 后不再被任何非 legacy 代码引用，无需锚点。

### 决策 3：五份文档 = 目标契约 + 现状映射（防双向漂移）

文档写的是**目标契约**（P3/P4/P10/P11 各自落地时对齐），若不标现状，Claude Code 会话会误以为 `context/runtime.py` 等已存在（正向漂移）；反之只写现状则失去冻结意义（反向漂移）。因此每份文档固定双段结构：

```markdown
> 状态: 冻结（P1，2026-08-26）— 目标契约。当前实现差距见「现状映射」节与各 Phase plan。
## （契约正文）
## 现状映射（截至 P1）
| 契约要素 | 现状 | 差距归属 Phase |
```

内容取自伞形 plan 对应章节（不新发明设计）：agent-flow ← §一/§三/§四；state-contract ← §五；memory-architecture ← §六；report-runtime ← §十二 + §四 Report Agent 段；frontend-contract ← §十六。图用 mermaid（GitHub/VSCode 可渲染），P15 才产出 7 张正式架构图，P1 文档内嵌图即满足「架构图完成」。

### 决策 4：CLAUDE.md 宪法版结构——架构章全新 + 操作区保留

重写 ≠ 清空。分两区：

- **宪法区（全新撰写）**，章节顺序：Project Identity → Architecture Principles（含 Forbidden Patterns 十条 + generic 文件夹禁令）→ Canonical Flow → Agent Responsibilities → State Contract → Memory Architecture → Tool & MCP Contract → LLM Policy（只写统一 reasoning model、provider 无关，不出现 MiniMax/Qwen 等 provider 名）→ Frontend Contract → Report Contract → Timeout & Failure Policy → Observability → Legacy Policy → Change Discipline（原 Planning Discipline + Phase 门纪律）。每章末尾加一行 `> 现状: …（P{n} 落地）` 防漂移。
- **操作区（保留现文，微调）**：沟通语言、配套文档指引、开发前必读（plan 驱动）、Setup and Commands、Testing、Configuration。Configuration 表保留 `MINIMAX_API_KEY` 等（P6 当天才换 `LLM_*`，宪法版只加一句「模型配置将于 P6 收敛为 LLM_*」）。历史实现叙事（两图链路细节、四层记忆实现细节等）压缩为一句话 + 指向五份文档，不再堆入 CLAUDE.md。

AGENTS.md / README.md / docs/sse-v2.md 一律不动（P15 收口）。

### 已识别的实现坑

- **db.py 相对路径**：`_DB_PATH = Path(__file__).parent.parent / "report.duckdb"` 在 `app/` 下指向 `backend/`；移到 `app/legacy/` 后须改为 `.parent.parent.parent`，否则 DuckDB 文件落到 `app/` 下。
- **`app/agent/__init__.py`**：现内容仅一行 re-export `build_parent_graph`；全仓无人走 `from app.agent import build_parent_graph` 路径（已核实），清空为空文件（仓库惯例：空 `__init__.py` 是有意的）。
- **vitest/lint 覆盖**：vitest glob `src/**/__tests__/*` 与 oxlint 天然覆盖 `src/legacy/`，移动后测试与 lint 照常生效，无需配置改动。
- **e2e 断言**：[test_full_flow.py:176-180](../backend/tests/e2e/test_full_flow.py#L176-L180) 断言 confirm 后 draft 为 `locked`，已被 b066e9c 的 `release_lock` 设计取代（成功后 `locked→complete`，幂等）；全文件仅此一处 `locked` 断言（已核实）。

---

## Files to change（任务分解）

### Task 0: e2e 陈旧断言小修（搭车项，独立 commit）

**Files:**
- Modify: `backend/tests/e2e/test_full_flow.py:176-180`

- [ ] **Step 1: 修改断言** — `status == "locked"` 改为 `status == "complete"`，注释更新为引用 b066e9c 的释放设计（confirm 成功 → `release_lock` 幂等释放 locked→complete）：

```python
        # After confirm succeeds, the draft lock is released (b066e9c:
        # release_lock flips locked -> complete, idempotent).
        assert snap["current_requirement"]["status"] == "complete", (
            f"expected latest draft to be complete after confirm, got "
            f"{snap['current_requirement']['status']}"
        )
```

- [ ] **Step 2: 离线回归** — `cd backend && D:/miniConda/envs/agent/python.exe -m pytest`（e2e 自动 skip，其余应全绿）。**如实记录**：e2e 本体需真实服务，实跑验证挂起到下次跑批窗口。
- [ ] **Step 3: Commit** — `fix(e2e): confirm 后 draft 断言 locked→complete 对齐 b066e9c 释放设计 + plan: p1-architecture-freeze`

### Task 1: 后端 legacy 归置

**Files:**
- Create: `backend/app/legacy/__init__.py`（空）、`backend/app/legacy/agents/__init__.py`（空）
- Move: `backend/app/agent/parent_graph.py` → `backend/app/legacy/agents/parent_graph.py`（git mv）
- Move: `backend/app/db.py` → `backend/app/legacy/db.py`（git mv）
- Modify: `backend/app/main.py`、`backend/app/agent/__init__.py`

- [ ] **Step 1: 建目录 + git mv**

```bash
mkdir backend/app/legacy backend/app/legacy/agents
touch backend/app/legacy/__init__.py backend/app/legacy/agents/__init__.py
git mv backend/app/agent/parent_graph.py backend/app/legacy/agents/parent_graph.py
git mv backend/app/db.py backend/app/legacy/db.py
```

- [ ] **Step 2: 修 db.py 路径** — `_DB_PATH`/`_SEED_SQL_PATH` 的 `.parent.parent` 改 `.parent.parent.parent`（决策「已识别的实现坑」第 1 条）。
- [ ] **Step 3: main.py 接线** — 顶部 import 改为桥接区结构；摘除 lifespan 的 `get_connection()` / `close_connection()` 两行及 `from app.db import ...`；删除无消费的 `AgentState` import；`from app.agent.parent_graph import build_parent_graph` 移入锚点区块改为 `from app.legacy.agents.parent_graph import build_parent_graph`；`_chat_legacy` / `_format_event` / `_build_response` / `_legacy_lock` / `_VALID_CHOSEN_TOOLS` 上方加统一的 LEGACY 区块注释（决策 2 原文，含 `LEGACY BRIDGE BEGIN` / `LEGACY BRIDGE END` 标记行——import 夹在两标记之间，区块内只放 import）。
- [ ] **Step 4: 清空 `app/agent/__init__.py`** 为空文件。
- [ ] **Step 5: 验证** — `cd backend && D:/miniConda/envs/agent/python.exe -m pytest`：P0 基线（382 passed）不回退、0 fail（若有测试隐式依赖 `app.agent.__init__` 的 re-export 或 lifespan 的 DuckDB，此处暴露并按最小改动修复——修复原则：改测试的 import 路径，不给 legacy 加回依赖）。
- [ ] **Step 6: Commit** — `refactor(legacy): parent_graph/db 归置 app/legacy，main.py 建 LEGACY BRIDGE 锚点 + plan: p1-architecture-freeze`

### Task 2: 前端 legacy 归置

**Files:**
- Create dir: `frontend/src/legacy/{pages,views,stores,api}/`
- Move: `pages/{ChatPage,TemplateCenter,HistoryPage,StandaloneReportPage}.tsx` → `legacy/pages/`
- Move: `pages/views/{ChatView,RunningView,ReportView}.tsx` → `legacy/views/`
- Move: `stores/session.ts` → `legacy/stores/session.ts`
- Move: `api/{chat.ts,legacyAdapter.ts}` → `legacy/api/`
- Move test: `api/__tests__/legacyAdapter.test.ts` → `legacy/api/__tests__/`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: git mv 九个源文件 + 一个测试文件**（目录结构见上；全部用 `git mv` 保历史）。
- [ ] **Step 2: 修相对导入** —— **原则：先移动、跑 `npm run build` 让 tsc 报错、按真实报错逐个修，不以本 plan 的示例路径为准机械字符串替换**。下面的映射只是预期方向的对账清单：
  - `legacy/pages/ChatPage.tsx` 等：`'../stores/session'` → `'../../stores/session'`、`'./views/X'` → `'../views/X'`、`'../stores/authStore'` → `'../../stores/authStore'`；
  - `legacy/stores/session.ts`：`'../api/chat'` → `'../api/chat'`（同深度，核验即可）、`'../types/...'` → `'../../types/...'`；
  - `legacy/api/chat.ts`：`'./sse'` → `'../../api/sse'`、`'../types/report'` → `'../../types/report'`、`'../stores/authStore'` → `'../../stores/authStore'`；
  - `legacy/api/legacyAdapter.ts`：无外部依赖，核验即可；
  - `App.tsx`：`'./pages/ChatPage'` → `'./legacy/pages/ChatPage'` 等 4 条 + 删除/更新「Legacy pages kept available during Phase 8 cleanup」注释为指向本 plan。
- [ ] **Step 3: 验证** —

```bash
cd frontend && npm run build    # tsc -b 抓所有断链（修 import 的唯一依据）
npm run lint && npm run test:run  # 基线不回退 + freeze 测试全过
```

- [ ] **Step 4: Commit** — `refactor(legacy): 前端旧页面/旧 store/旧 SSE client 归置 src/legacy + plan: p1-architecture-freeze`

### Task 3: import 断言测试（双侧钉子）

**Files:**
- Create: `backend/tests/contracts/test_legacy_import_freeze.py`
- Create: `frontend/src/legacy/__tests__/legacyImportFreeze.test.ts`

- [ ] **Step 1: 写后端断言测试**（AST 扫描，锚点区豁免 + 快照防扩容）：

```python
"""P1 Legacy Import Freeze（docs/plans/2026-08-26-p1-architecture-freeze.md 决策 2）。

规则：
1. backend/app 与 backend/tests 下，LEGACY BRIDGE 区之外禁止 import app.legacy*；
2. main.py 必须恰好一对 LEGACY BRIDGE BEGIN / END 标记，区间内只允许 import 语句，
   且 import 集合等于快照（禁止悄悄扩容）。
离线可跑：纯 AST/文本扫描，不触 PG / LLM。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TESTS_DIR = Path(__file__).resolve().parents[1]
MAIN_PY = APP_DIR / "main.py"

BRIDGE_BEGIN = "LEGACY BRIDGE BEGIN"
BRIDGE_END = "LEGACY BRIDGE END"
# main.py 桥接区允许的唯一 import 快照（Task 1 落地后按实际行核对——
# db.py 摘除干净则只有这一条；确需新增时先改快照并过评审）。
ALLOWED_BRIDGE_IMPORTS: frozenset[str] = frozenset({"app.legacy.agents.parent_graph"})


def _bridge_span(lines: list[str]) -> tuple[int, int]:
    """返回 (begin_idx, end_idx)：BEGIN 与 END 标记行的下标。
    缺失、不成对或顺序错误直接断言失败——区块是显式标记界定的，
    不做任何「注释到第一条非注释行为止」式的文本推断。"""
    begins = [i for i, ln in enumerate(lines) if BRIDGE_BEGIN in ln]
    ends = [i for i, ln in enumerate(lines) if BRIDGE_END in ln]
    assert len(begins) == 1 and len(ends) == 1 and begins[0] < ends[0], (
        f"main.py 必须恰好有一对 {BRIDGE_BEGIN}/{BRIDGE_END} 标记且顺序正确"
    )
    return begins[0], ends[0]


def _is_legacy_import(node: ast.stmt) -> bool:
    if isinstance(node, ast.ImportFrom):
        return node.module is not None and (
            node.module == "app.legacy" or node.module.startswith("app.legacy.")
        )
    if isinstance(node, ast.Import):
        return any(
            a.name == "app.legacy" or a.name.startswith("app.legacy.")
            for a in node.names
        )
    return False


def _import_nodes(tree: ast.AST) -> list[ast.stmt]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_no_legacy_imports_outside_bridge():
    violations: list[str] = []
    legacy_pkg = (APP_DIR / "legacy").resolve()

    def iter_py(root: Path):
        for p in root.rglob("*.py"):
            if "__pycache__" not in p.parts and legacy_pkg not in p.resolve().parents:
                yield p

    # 1) main.py：剔除桥接区行后再全文件扫描
    main_lines = MAIN_PY.read_text(encoding="utf-8").splitlines()
    begin, end = _bridge_span(main_lines)
    outside_main = "
".join(main_lines[:begin]) + "
" + "
".join(main_lines[end + 1:])
    for n in _import_nodes(ast.parse(outside_main)):
        if _is_legacy_import(n):
            violations.append(f"app/main.py: {ast.dump(n)[:80]}")

    # 2) 其余 app/ + tests/：整文件扫描（legacy 包自身互引已豁免）
    for root in (APP_DIR, TESTS_DIR):
        for py in iter_py(root):
            if py.resolve() == MAIN_PY.resolve():
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            rel = py.relative_to(APP_DIR.parent)
            for n in _import_nodes(tree):
                if _is_legacy_import(n):
                    violations.append(f"{rel}: {ast.dump(n)[:80]}")

    assert not violations, (
        "新代码禁止 import legacy（P1 冻结，决策 2）:
" + "
".join(violations)
    )


def test_bridge_imports_frozen():
    lines = MAIN_PY.read_text(encoding="utf-8").splitlines()
    begin, end = _bridge_span(lines)
    inner = ast.parse("
".join(lines[begin + 1 : end]))
    non_import = [n for n in inner.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not non_import, (
        f"桥接区内只允许 import 语句，发现: {[type(n).__name__ for n in non_import]}"
    )
    normalized: set[str] = set()
    for n in inner.body:
        if isinstance(n, ast.ImportFrom) and n.module:
            normalized.add(n.module)
        elif isinstance(n, ast.Import):
            normalized.update(a.name for a in n.names)
    assert normalized == set(ALLOWED_BRIDGE_IMPORTS), (
        f"LEGACY BRIDGE 快照漂移: 现为 {sorted(normalized)}, "
        f"允许 {sorted(ALLOWED_BRIDGE_IMPORTS)}。禁止扩大桥接区——"
        "如确需新增，先改本测试快照并过评审。"
    )

（`ALLOWED_BRIDGE_IMPORTS` 以 Task 1 实际落地行为准核对；若 `db.py` 摘除后确有第二条必要引用，执行时更新快照并在 commit message 说明理由。）

- [ ] **Step 2: 写前端断言测试**（Node fs 文本扫描，src 下 legacy/ 之外禁止 `from '.../legacy/`）：

```typescript
/**
 * P1 Legacy Import Freeze — frontend 侧。
 * src/ 下除 legacy/ 自身外，禁止任何文件 import 进入 legacy/。
 * 纯 Node fs 扫描，无渲染、无网络。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', '..')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) {
      if (name === '__pycache__' || name === 'node_modules') return []
      if (relative(SRC, p).startsWith('legacy')) return [] // legacy 自身豁免
      return walk(p)
    }
    return /\.(ts|tsx)$/.test(name) ? [p] : []
  })
}

describe('legacy import freeze', () => {
  it('no file outside src/legacy imports from legacy/', () => {
    const violations: string[] = []
    for (const f of walk(SRC)) {
      const text = readFileSync(f, 'utf-8')
      if (/(?:from\s+|import\()['"][^'"]*\/legacy\//.test(text)) {
        violations.push(relative(SRC, f))
      }
    }
    expect(violations, `新代码禁止 import src/legacy（P1 冻结）:\n${violations.join('\n')}`).toEqual([])
  })

  it('legacy/ directory exists (归置已完成)', () => {
    expect(statSync(join(SRC, 'legacy')).isDirectory()).toBe(true)
  })
})
```

- [ ] **Step 3: red 验证（证明钉子真能扎人）** — 临时在 `backend/app/services/report_version_service.py` 尾部加 `from app.legacy.db import get_connection  # TEMP`、在前端 `frontend/src/api/sse.ts` 加 `import x from './../legacy/api/chat'`（TEMP），分别跑两侧断言测试，预期 FAIL 并报出这两个文件；随后删除临时行，复跑 PASS。
- [ ] **Step 4: 全量回归** — 后端 pytest 全量 + 前端 `npm run lint && npm run test:run` 全绿。
- [ ] **Step 5: Commit** — `test(contracts): legacy import freeze 双侧断言（锚点豁免 + 快照防扩容） + plan: p1-architecture-freeze`

### Task 4: 五份架构契约文档

**Files:**
- Create: `docs/architecture/agent-flow.md`
- Create: `docs/architecture/state-contract.md`
- Create: `docs/architecture/memory-architecture.md`
- Create: `docs/architecture/frontend-contract.md`
- Create: `docs/architecture/report-runtime.md`

每份固定双段结构（决策 3）：`> 状态: 冻结（P1，2026-08-26）` + 契约正文 + 「现状映射（截至 P1）」表（契约要素 | 现状 | 差距归属 Phase）。内容**取自伞形 plan 对应章节原文的设计决断，不新发明**。各文档必含小节与素材：

- [ ] **Step 1: agent-flow.md**（← 伞形 §一/§三/§四）
  - 定位声明（Stateful Agentic Data Analysis Workbench）+ 项目性质约束（个人面试项目）；
  - 核心原则代码块（Agentic where uncertainty exists / Deterministic where correctness matters）；
  - Agent ≠ Workflow：Requirement Workflow / Requirement Agent、Execution Workflow / Execution Agent 嵌套图（伞形 §三 原文）；
  - Canonical Flow mermaid 图（伞形 §三 → 转 mermaid flowchart）；
  - 三 Agent 职责表（负责 | 禁止，伞形 §四 原文）+ Execution Repair 六要素上下文（Original Requirement / Current Schema / Previous SQL / Failure Category / Error Message / Retry Count）与 `MAX_SQL_REPAIR_RETRIES` 上限；
  - 现状映射：requirement_analysis_graph（SQL gate 由 test_requirement_analysis_sqlgate 钉住）/ confirmed_execution_graph / sql_graph 对应哪段契约；Execution Agent 动态决策环尚未成形 → P8。
- [ ] **Step 2: state-contract.md**（← 伞形 §五）
  - 五块 State 全字段定义（RequestState / RequirementState / ExecutionState / ReportState / RuntimeState，字段名逐一列出，标 `original_query` immutable）；
  - 字段所有权规则（谁写、谁读、何时清零）；
  - 现状映射：现役 `SQLAgentState` / `ConfirmedExecutionState` / `AgentState`(legacy) → 五块的归属拆分；拆分动作属 P3，checkpoint 序列化兼容性风险记入 P3 plan 输入。
- [ ] **Step 3: memory-architecture.md**（← 伞形 §六）
  - 四类记忆职责表（Session / Conversation / Semantic / Query + 关键规则，伞形表格原文）；
  - Recall Before Agent 时序图 + Selective Recall 四触发 / 四不召回；
  - Agent-specific Policy 表（Requirement/Execution/Report × Conversation/Semantic/Query）；
  - Write After Reliable Event + Query Memory 写入门槛（SUCCESS 且 SUCCESS；失败走 `QueryMemory.record_failure()`）；
  - V1 简化冻结三条（不做 promotion pipeline / confidence 规则固定 / temporary_preference 绑 session_id 存 `agent.session`）；
  - Lifecycle 状态机（candidate/active/superseded/expired + INSERT/UPDATE/SUPERSEDE/EXPIRE/DELETE）+ semantic_entry 补字段清单；
  - Conflict Priority 固定序（Current User Requirement > Current DB Schema > Business Definition > Stable Preference > Query Experience > Conversation Summary；Schema 永不被 Memory 覆盖）；
  - context runtime 四文件接口（runtime.py / policy.py / decision.py / assembler.py 签名级描述）；
  - 现状映射：`app/context.py` L1/L2/L2.5/L3 与 `app/infra/memory/` 已是底座 → P4 组织为 memory/ 包。
- [ ] **Step 4: frontend-contract.md**（← 伞形 §十六）
  - AnalysisPhase 状态机 mermaid 图（idle → parsing → awaiting_missing → awaiting_confirm → generating → report_ready；error / adjusting 异常支）；
  - `analysisReducer` 单写者原则 + discriminated-union dispatch；
  - Canonical Events 七事件基线表（phase / requirement / trace / thinking / report / error / done，各 payload 要点，锚定 `docs/sse-v2.md`）；
  - P11 扩展预告（progress 事件族：agent.* / tool.* / sql.* / repair.* / report.*——扩展不替换）;
  - ReportVersion 一级 Domain Object（GENERATING/DONE/ERROR 与 SUCCESS/EMPTY/FAILED 两组状态区分）+ V1 能力范围（创建/查看/切换/继续调整/重新生成；diff/favorite/delete 属 V2）；
  - 现状映射：reducer/confirmStream 已合规；progress 事件未实现 → P11。
- [ ] **Step 5: report-runtime.md**（← 伞形 §十二 + §四 Report Agent 段）
  - ReportSpec 支持字段枚举（title/summary/insight/kpi/chart/table/section/recommendation/alert）；
  - 数据真实性原则（数值/排名/统计/图表数据必须来自 Query Result）；
  - 三层 Validator（结构校验 / 数值校验 / 禁止自由生成——对象是 `ReportSpec → QueryResult` 映射而非渲染 HTML，明确不做 HTML 正则审计及原因）；
  - ReportVersion append-only 语义（SUCCESS/EMPTY/FAILED 全部落库：`persist_confirmed_run / persist_adjust_run / persist_empty_run / persist_error_run`）；
  - 现状映射：`services/report_version_service.py` 三态落库已在位；ReportSpec Validator 三层未成形 → P10。
- [ ] **Step 6: Commit** — `docs(architecture): P1 五份架构契约文档（agent-flow/state/memory/frontend/report） + plan: p1-architecture-freeze`

### Task 5: CLAUDE.md 宪法版重写

**Files:**
- Modify: `CLAUDE.md`（整文件重写）

- [ ] **Step 1: 按「决策 4」结构重写**。**CLAUDE.md 不是 architecture spec——只写 invariant / forbidden patterns / canonical flow / ownership / change discipline，任何实现细节一律以一两句话 + 相对链接指向 docs/architecture/* 对应文档。** 宪法区 14 章每章末尾加 `> 现状: …（P{n} 落地）` 行；五份文档以相对链接挂入对应章节。Forbidden Patterns 十条原文写入 Architecture Principles 章：

```markdown
## Forbidden Patterns（冻结，违反即打回）

- 不直接 import RAG 项目代码——只经 `MCP Client → RAG MCP Server`
- 不让 Agent 自拼 Context——Context Runtime 是唯一入口（P3 落地前沿用 build_session_context）
- 不让 Agent 直接访问 Memory DB——读写一律经 Memory Manager
- 不让 Agent 直接调用 provider SDK——只依赖 LLM Adapter（P6 前：app/llm.py call_llm）
- 不让 Tool 没有 description——Tool Description 是 Agent Contract
- 不让 Report Agent 编造数据——一切数值来自 Query Result
- 不无限 retry——预算固定 SQL 2 / MCP 2 / LLM 2
- 不绕过 MCP 直连 RAG 内部机制（embedding/vector_search/chunk/rerank 不进 ReportAgent）
- 不新增 legacy import——LEGACY BRIDGE 锚点区外零豁免（tests/contracts/test_legacy_import_freeze.py 钉住）
- 不新建 utils2/ managers/ runtime/ helpers/ common2/ 类 generic 文件夹——代码放最窄既有域边界
```

  LLM Policy 章只写「统一 reasoning-capable model、provider 无关、八件事（Provider/Model/Base URL/Auth/Generation Config/Structured Output/Reasoning Normalization/Retry/Timeout）于 P6 收敛为 `llm/` Adapter；配置届时从 MINIMAX_* 收敛为 LLM_*」。
  操作区保留清单：沟通语言 / 配套文档 / 开发前必读（plan 驱动）/ Setup and Commands / Testing / Configuration（加 P6 收敛注记）/ Planning Discipline（并入 Change Discipline 章，内容不减）。
  删除的内容：Project Overview 中的现状架构叙述、Workbench request flow 实现细节、Memory 四层实现细节、Checkpoint/Observability 实现细节——各压缩为一两句 + 链接到五份文档；「Many Python files are TSD-encrypted」提示保留在操作区。
- [ ] **Step 2: 校验** — 文内所有相对链接可达（五份文档、AGENTS.md、README.md、docs/plans/README.md、startup_guard.py 等）；无 provider 名泄漏进 LLM Policy 章（grep `MiniMax|Qwen|SiliconFlow|minimax` 于新写的宪法区应为 0 命中，Configuration 表操作区除外）。
- [ ] **Step 3: Commit** — `docs(constitution): CLAUDE.md 宪法版重写（14 章 + Forbidden Patterns + 现状标注） + plan: p1-architecture-freeze`

### Task 6: 索引登记 + P1 验收核对

**Files:**
- Modify: `docs/plans/README.md`、`docs/plans/2026-08-26-p1-architecture-freeze.md`

- [ ] **Step 1: plans README 登记**——进行中区加一行本 plan；完成后移入已完成表（带 commit 与落地摘要）。
- [ ] **Step 2: 伞形 plan P1 验收清单核对**——逐项打勾并回填证据（文档 5 份路径 / 断言测试路径 / CLAUDE.md 章节 / `git log --oneline -- backend/app/legacy frontend/src/legacy` 输出）；伞形 plan §十八 P1 行补「实施 plan: p1-architecture-freeze」回链。
- [ ] **Step 3: 最终全量回归** —

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest        # P0 基线不回退（0 fail）+ freeze 测试全过
cd frontend && npm run lint && npm run test:run                   # P0 基线不回退（0 fail）+ freeze 测试全过
cd frontend && npm run build                                      # tsc 干净
```

- [ ] **Step 4: Commit** — `docs(plans): P1 落地登记 + 验收清单核对 + plan: p1-architecture-freeze`

### 待下次跑批窗口的验证（本 plan 不阻塞于此，如实挂起）

- 服务按序重启（MCP server → uvicorn :8100 → vite :3000）后手工冒烟：workbench 新建分析 → Requirement → Confirm → 报告；`/history`、`/legacy/chat`、`/legacy/templates` 三条旧路由仍可达（归置不删功能）；
- `REPORTAGENT_E2E=1 python -m pytest backend/tests/e2e/test_full_flow.py -s`（含 Task 0 新断言）。

---

## 复用现有工具

- **git mv**：全部移动保历史，`git log --follow` 可审计；
- **pytest 基础设施**：[conftest.py](../../backend/tests/conftest.py) 的 sys.path/dotenv 装载、markers（smoke|contracts|graphs|persistence|api|e2e）——freeze 测试挂 `contracts` marker，放 `backend/tests/contracts/`；
- **vitest glob** `src/**/__tests__/*.{test,spec}.{ts,tsx}`：天然覆盖 `src/legacy/`，零配置；
- **既有钉子测试风格**：[test_requirement_analysis_sqlgate.py](../../backend/tests/graphs/test_requirement_analysis_sqlgate.py)（结构性断言钉边界）作为 freeze 测试的写法范本；
- **伞形 plan 素材直采**：五份文档的图/表/字段全部来自 [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §一~§十七，不新发明设计；
- **P0 基线数字**：382 passed（后端）/ 256 passed（前端）仅作参照快照，回归红线是「基线不回退 + freeze 测试全过」（[baseline-2026-08-25.md](../../evaluation/results/baseline-2026-08-25.md)）。

---

## Verification

1. **每 Task 即时验证**（上文各 Step 内命令）：pytest 全量 / tsc -b / oxlint / vitest 全量。红线口径 = **P0 基线不回退（0 fail，skip 数变化须可解释）+ 新增 freeze 测试全部通过**；测试绝对数字不是契约（382/256 只是 P0 快照参照）。
2. **断言测试 red 演示**：Task 3 Step 3 的临时违规注入必须先红后绿，证明钉子有效——「断言测试永远绿」不算数。
3. **provider 泄漏检查**：`grep -rnE "MiniMax|Qwen|SiliconFlow" CLAUDE.md docs/architecture/` 在宪法区零命中（Configuration 操作区表与伞形 plan 附录 D 不受限）。
4. **链接完整性**：CLAUDE.md 与五份文档内的相对路径逐一可达（手工 + 编辑器跳转）。
5. **挂起项**（服务未启动，不阻塞本 plan 完成宣告）：真实浏览器冒烟矩阵 + e2e 实跑（见 Task 分解末尾），下次跑批窗口补做并回填结果到本 plan 完成报告。

---

## 明确不做

- ❌ **不删除任何 legacy 代码** —— P1 只归置 + 钉住，删除是 P15（伞形 §十七）。
- ❌ **不动 data_graph / intent.py / requirement_parser / requirement_options / sql_graph** —— 名字旧但现役共用（决策 1 B 清单），动了就破坏 requirement/confirmed 链路。
- ❌ **不做 State 五块真实拆分** —— state-contract.md 只冻结契约，拆分是 P3。
- ❌ **不新建 context/ memory/ reliability/ llm/ 目录** —— 各归 P3/P4/P9/P6，P1 只写文档冻结接口形态。
- ❌ **不接 Langfuse、不加 progress SSE 事件** —— P13 / P11。
- ❌ **不刷新 README.md / AGENTS.md / docs/sse-v2.md** —— P15 文档收口。
- ❌ **不做 PHASE2_MCP_ONLY flag** —— P2 的活。
- ❌ **不为「归置更彻底」把 _chat_legacy 硬搬出 main.py** —— 会造出冻结结构外的 legacy/api 子目录 + 全局 _agent 注入改造，收益不抵风险；LEGACY BRIDGE 锚点是显式、可测、禁扩容的豁免，足够。
- ❌ **不在本 plan 内实跑 e2e / 浏览器冒烟** —— 后台服务已停（用户告知），离线手段已全覆盖；实跑挂起至下次跑批窗口并如实回填。
