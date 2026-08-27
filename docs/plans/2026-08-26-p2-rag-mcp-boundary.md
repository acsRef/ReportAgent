# P2 RAG / MCP Boundary 实施 plan

> 状态: 进行中（Task 1+2 已落地合 master；Task 3/4/5 待开）
> 上游: [2026-08-25-refactor-master-freeze.md](2026-08-25-refactor-master-freeze.md) §二（系统边界）/ §八（Tool/MCP Contract）/ §十八 P2 验收清单
> 前置: P1 Architecture Freeze 已落地（26d900c..dbad62a，legacy 冻结面 + 宪法版 CLAUDE.md 在位）

## 落地记录（每次 commit 后追加）

**Task 1 — 泛化 stdio MCP client + 五分类失败语义**
- 落地：master `aa73a51`（merge commit），feat: `8adfbf6`
- 4 轮 review 全部消化（async lifecycle cleanup / timeout cancel + drain / per-invocation ownership / done_event drain）
- 关键产物：`backend/app/tools/mcp_client.py` + `mcp_errors.py`（529 + 23 行）；`mcp_faq_client.py` 改 shim

**Task 2 — schema/字典/FAQ 三通道切 MCP 正路 + faq catch 收紧 + graph 改走 registry**
- 落地：master `bc5159c`（merge commit），feat: `eab2849` + 4 轮 review fix（`45ba058` / `2f3badb` / `9bfd502` / `aee59b7`）
- 关键产物：`rag_schema.py` / `interface_dict_tools.py` / `faq_tools.py` MCP-first dispatcher；Tool Contract 统一 `{question, text, score}`；graph 改走 registry；mcp_client boundary 收口（_validate_matches_contract 校验 + normalize + strip 内部字段）
- 测试：475 passed（vs P1 基线 384，新增 91 个 dispatcher / validation / autouse 测试）

**挂起项**：
1. `_strip_internal_fields` 当前是 denylist（黑名单）—— 后续 cleanup 可改 stable-field allowlist（review 第 3 轮 P2 指出，非当前 blocker）
2. 真实跨进程 MCP 验证（ragent-py + backend 联合跑通 search_dictionary / search_faq）+ e2e 补跑 → 跑批窗口补做

**Task 3 Step 1 — import boundary 钉子**
- 落地：master `25b29b0`（merge），feat: `b17d987`
- `tests/contracts/test_mcp_boundary_freeze.py` 4 钉子（tools/ 外禁 import rag_schema/interface_dict_tools/mcp_client/mcp_errors；禁真 import mcp_server；非 tools/ 禁硬编码 ragent-py 路径）；red 验证注入 sql_graph 违规 import 命中后撤除

**Task 3 Step 2 — tool allowlist 钉子（review 第 1 轮 P1 修订：source 语义定死）**
- 分支 `p2-task3`：`b75ce55` + review 修订 `8277129`；master `6498d12`（merge）
- **决议（取代 Step 2 原文「schema 三工具 source=='mcp'」的过宽表述）**：`metadata.source` 语义 = **「该 Tool 请求满足时的实际正路 runtime 通道」**，不是 capability 上游来源。据此：
  - `search_tables` / `get_table_ddl` → `"mcp"`（MCP-first dispatcher，ragent-py search_dictionary 通道）
  - `list_tables` → `"local"`（无 MCP 等价工具，正路 `_list_dict_docs` HTTP 直连，rag_schema.py:16/:157 实证；标 mcp 即 metadata 对 runtime 撒谎，trace 观测错误）
  - `search_interface_dictionary` / `search_faq` 同为 MCP-first，但 source 标注策略 **Task 4 mcp-contract.md 定夺**，Step 2 钉子暂不约束
- 钉子 6 个：白名单 12 工具双向断言 / source ∈ {local,mcp} / MCP-first 标 mcp / **HTTP 直连反向钉 local** / 禁入 RAG 内部机制工具名（embedding/vector_search/rerank/chunk/ingest/upsert/list_docs/query_pgvector/kb_manage 子串）/ description 含「用于：」
- P2 修订：allowlist fixture 改为「清空重建 + 退出恢复快照」——抗跨测试全局 registry 污染（探针测试先行注入 junk tool，allowlist 仍绿验证）
- 回归：503 passed + 1 skipped

**Task 3 Step 3 — schema contract 钉子**
- 分支 `p2-task3`：review 修订 `???`（待 commit）
- `tests/contracts/test_mcp_contract_schema.py` 9 钉子，**与 Task 2 TestValidateMatchesContract 14 例分工**——后者是 helper 行为级，前者是契约面冻结：
  - 钉子 1：`_INTERNAL_RESULT_FIELDS` 快照冻结（5 字段：chunk_id/document_id/embedding/rerank_score/kb_id）——同 LEGACY BRIDGE 快照手法，增删须同步 Task 4 文档
  - 钉子 1b：稳定表与内部表不相交——同一字段既「Agent 可依赖」又「boundary strip」是契约自相矛盾
  - 钉子 2：完整 boundary 链（call_tool → _validate_matches_contract 即 tool 层实际使用面）端到端剥离内部字段；同时不许出现稳定表之外字段
  - 钉子 3：参数化 4 例（缺 text / 缺 score / text 非 str / score 为 None）端到端 → MCP_INVALID_RESPONSE
  - 钉子 4：EMPTY_RESULT（matches=[]）合法端到端；schema drift（`{}` / `{results:[]}`）伪装成空命中则 INVALID（端到端冻结 Task 2 review 第 3 轮 P1 分界）
- **production 代码零改动**——所有断言对当前 Task 2 实现的契约面成立
- red 验证：临时把 `kb_id` 从 denylist 移除 → 钉子 1（快照漂移）+ 钉子 2（kb_id 泄漏 + 出现稳定表之外字段）同时 red → 撤除 → green
- 回归：512 passed + 1 skipped（503 + 9 新增）
- **决议（取代 Step 2 原文「schema 三工具 source=='mcp'」的过宽表述）**：`metadata.source` 语义 = **「该 Tool 请求满足时的实际正路 runtime 通道」**，不是 capability 上游来源。据此：
  - `search_tables` / `get_table_ddl` → `"mcp"`（MCP-first dispatcher，ragent-py search_dictionary 通道）
  - `list_tables` → `"local"`（无 MCP 等价工具，正路 `_list_dict_docs` HTTP 直连，rag_schema.py:16/:157 实证；标 mcp 即 metadata 对 runtime 撒谎，trace 观测错误）
  - `search_interface_dictionary` / `search_faq` 同为 MCP-first，但 source 标注策略 **Task 4 mcp-contract.md 定夺**，Step 2 钉子暂不约束
- 钉子 6 个：白名单 12 工具双向断言 / source ∈ {local,mcp} / MCP-first 标 mcp / **HTTP 直连反向钉 local** / 禁入 RAG 内部机制工具名（embedding/vector_search/rerank/chunk/ingest/upsert/list_docs/query_pgvector/kb_manage 子串）/ description 含「用于：」
- P2 修订：allowlist fixture 改为「清空重建 + 退出恢复快照」——抗跨测试全局 registry 污染（探针测试先行注入 junk tool，allowlist 仍绿验证）
- 回归：503 passed + 1 skipped

## Preconditions（P1 已冻结，P2 不重判）

1. `frontend/src/legacy/components/chat/{AgentTimeline,ChatCards,EmptyState}.tsx` 属 legacy 冻结面（P1 执行中按「引用关系判定」追加认定，见 p1 plan 落地记录偏差①）。
2. `frontend/src/App.tsx` 是 legacy route bridge（`/legacy/chat` 等三条旧路由入口），其 legacy imports 受前端 freeze 测试显式快照保护——P2 不动 App.tsx。
3. 后端 LEGACY BRIDGE BEGIN/END 锚点区快照 = `{app.legacy.agents.parent_graph}`，禁扩容。
4. 名字像旧但现役、不许动的：data_graph / intent.py / requirement_parser / requirement_options / sql_graph._intent_analyze。
5. 回归红线口径：P0/P1 基线不回退 + freeze 测试全过（后端 384 passed / 前端 259 passed 为参照快照）。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「ReportAgent 只经 MCP 消费 RAG retrieval capability」从宪法条文变成代码结构 + Tool Contract + schema 契约 + failure 语义 + 四类测试钉子；`PHASE2_MCP_ONLY` flag 受控切换，Phase 5 收口删 fallback。

**Architecture:** 不新建传输层——复用 mcp_faq_client 的 stdio 单例模式泛化为通用 MCP client；ragent-py MCP server 已暴露的 `search_dictionary` 工具就是 schema/字典检索的 MCP 面。P2 做的是**换通道 + 钉边界 + 定契约**：schema 三工具与 FAQ 从 httpx 直连 ragent-py HTTP 切到 MCP client，直连实现降级为 fallback 并用 import 断言禁止新代码依赖；失败语义从「静默空数组」升级为显式 unavailable 标记。

**Tech Stack:** Python 3.11 (`D:/miniConda/envs/agent/python.exe`) + pytest + mcp>=1.0.0（已有依赖）；测试全部离线（mock HTTP / mock MCP session）。

---

## Context

### 为什么现在做

伞形 plan §二冻结了系统边界：RAG 项目（ragent-py）定位 Retrieval Runtime，ReportAgent 定位 Agent/Application Runtime，后者不得重新实现 Embedding / Chunking / Vector Search / Reranking，只通过 `MCP Client → RAG MCP Server` 使用。P1 宪法 Forbidden Patterns 第 1/8 条已写入：「不直接 import RAG 项目代码」「不绕过 MCP 直连 RAG 内部机制」。但当前代码的真实状态是：

### 现状盘点（2026-08-26 探查，全部核实）

ReportAgent 现有 **四条独立的 ragent-py 访问通道**，两条 HTTP 直连、一条 stdio MCP、一条本仓内自建 MCP server：

| # | 通道 | 文件 | 协议 | 服务对象 |
|---|---|---|---|---|
| 1 | 字典 KB HTTP 直连 | `backend/app/tools/interface_dict_tools.py` → ragent-py `/api/v1/retrieve` | httpx 直连 REST | search_interface_dictionary 工具（requirement 图意图分类用） |
| 2 | Schema KB HTTP 直连 | `backend/app/tools/rag_schema.py` → ragent-py `/api/v1/retrieve` + `/api/v1/documents` | httpx 直连 REST | search_tables / get_table_ddl / list_tables 三工具（data_graph → requirement/confirmed 两图共用） |
| 3 | FAQ stdio MCP | `backend/app/tools/mcp_faq_client.py` → ragent-py `mcp_server.server` 子进程 | **MCP stdio** | search_faq 工具（sql_graph prompt 注入） |
| 4 | 本仓 schema MCP server | `mcp_schema_server/server.py` + `registry.py`（registry 内部又是 httpx 直连 ragent-py） | 本仓自建 MCP server（stdio） | **无 backend 消费者**——backend/app 内 grep 无任何 client 连它；CLAUDE.md 说「backend discovers it through MCP」与实际不符 |

关键问题：

1. **双 Retrieval 分叉正在发生**：#1/#2 直接调 ragent-py REST `/api/v1/retrieve`（绕过 MCP），#3 走 MCP——同一个 RAG 能力两套访问语义并存，正是伞形 plan 点名的「双 Retrieval 系统永久分叉」风险。
2. **失败语义违反宪法**：#1/#2 失败时静默返回 `[]`/`None`（"ragent-py 不可达 → 返回空数组，不报错"）——伞形 plan §八明令「MCP 失败不许默默返回空数组伪装没结果」。
3. **chunk 解析逻辑泄漏**：`rag_schema._parse_table_doc` 在 ReportAgent 侧解析 ragent-py 的 chunk 文本格式（`# 表 \`public.fact_sales\``…），RAG 内部 chunking 格式成了跨仓库隐式契约；`mcp_schema_server/registry.py` 还有第二份相同解析。
4. **无 flag、无断言**：没有 `PHASE2_MCP_ONLY`；没有任何测试钉住「ReportAgent 不直连 ragent-py REST」。

### 用户原始诉求（2026-08-26）

> 直接开 P2 实施 plan……P2 最重要的不是"接 MCP"，而是把这个架构原则真正钉死：ReportAgent 不能知道 RAG 内部怎么做。至少覆盖 6 件事：MCP Boundary / Tool Contract / 输入输出 schema / Failure-Timeout-Retry / 测试钉子 / RAG 与 ReportAgent 职责划分。写出来以后先不要马上开工，重点审三个东西：boundary 是否够硬、Tool Contract 是否过度暴露内部、是否与 P3 职责重叠。

---

## 设计

### 决策 1：唯一通道 = 泛化的 stdio MCP client；HTTP 直连降级为 contract 一致的 fallback

**目标形态**：

```text
ReportAgent (tools/*)
    │  唯一正路：mcp_client.call_tool(name, args)
    ▼
ragent-py mcp_server (stdio, 已有子进程)
    ├── search_dictionary   ← schema/字典检索统一入口（ragent-py 已实现并暴露）
    └── search_faq          ← FAQ 检索（现 mcp_faq_client 已在用）
    │  fallback（flag 放行时）：httpx 直连 /api/v1/retrieve（现 rag_schema/interface_dict_tools 逻辑）
```

- **新建 `backend/app/tools/mcp_client.py`**：把 `mcp_faq_client.py` 的「后台事件循环线程 + stdio 会话单例 + 同步 call_tool 入口 + close 幂等清理」泛化成多工具版本。FAQ client 不重写——`mcp_faq_client.MCPFaqClient` 改为薄封装或直接被泛化 client 吸收（保留 `get_mcp_faq_client()` 兼容面，faq_tools 不感知）。同一进程一个 stdio 会话服务所有工具调用。
- **`search_dictionary` 对齐**：ragent-py MCP server 已暴露 `search_dictionary`（检索字典 KB）。schema 三工具与 search_interface_dictionary 的正路统一走它；返回的 chunk 文本解析（`_parse_table_doc`）留在 ReportAgent 侧——这是**渲染格式契约**而非 RAG 内部机制（chunk 由 ragent-py 的 render.py 渲染成 markdown 文档，属于其对外文档协议），但收敛为单一副本。
- **fallback 收敛**：现有 httpx 直连代码不删除（Phase 5 才收口），但从「默认路径」降为「flag 放行时的 fallback」，且四条通道的 fallback 行为对齐同一契约（输入/输出/错误语义一致）。`PHASE2_MCP_ONLY=true` 时 fallback 关闭，失败显式上抛。
- **明确不做**：不动 `mcp_schema_server/`（本仓自建 server 无消费者，是历史资产——P5 判定去留，本 plan 仅在文档标注）；不动 ragent-py 仓库任何代码。

### 决策 2：Tool Contract——只暴露业务能力，不暴露 RAG 机制

Agent 可见的工具面维持四个，全部是业务语义：

```text
search_tables(query, top_k) → [{table_name, description, columns, ddl, score}]
get_table_ddl(table_name)   → CREATE TABLE text
list_tables()               → [{table_name, description, column_count}]
search_interface_dictionary(query, top_k) → {matches:[{text, source, score}]}
search_faq(query, top_k)    → {matches:[{question, text, score}]}
```

Agent 永远看不到的工具面（RAG 内部机制，禁止出现在任何 tool/prompt/入参里）：`embedding() / vector_search() / rerank() / chunk() / query_pgvector() / ingest_*() / upsert_*() / list_*_docs() / kb 管理`。ragent-py MCP server 暴露的 ingest/upsert/list_docs 七个工具是**管理面**，ReportAgent 的 tool allowlist 测试只放行两个检索工具。

### 决策 3：I/O schema 契约——稳定字段 vs 内部字段

`docs/architecture/mcp-contract.md` 固定下表（P2 新增第六份架构文档，伞形 §八要求 input/output schema 固定）：

| 方向 | 字段 | 稳定性 |
|---|---|---|
| request（稳定契约） | `query: str` / `top_k: int` / `kb_ids: list[str]`（由 client 按 DICT_KB_NAME 解析，Agent 不传） | Agent 可依赖 |
| response（稳定契约） | `items[]: {text: str, score: float, title?: str}` | Agent 可依赖 |
| response（内部字段，禁止依赖） | `chunk_id / document_id / embedding / rerank_score / kb_id` 等 ragent-py 内部字段——client 层过滤，不透给工具层 | 随时可变 |

规范化职责在 `mcp_client.py` 一处完成（现 faq_tools._mcp_search_faq 的归一逻辑上移），tool 层只见稳定契约。

### 决策 4：Failure 语义五分类（ErrorEnvelope 前身，先落 enum + 显式标记）

| 情形 | 行为 |
|---|---|
| `MCP_TIMEOUT` | 重试预算内 retry（固定 2 次）；仍败 → 显式 unavailable 上抛，不伪装空结果 |
| `MCP_UNAVAILABLE`（连接/握手失败） | flag 未锁 → fallback；flag 锁定 → 显式上抛 |
| `MCP_INVALID_RESPONSE`（非 JSON/结构漂移） | 不 fallback（重试同结果）；记 trace + 显式错误 |
| `EMPTY_RESULT`（正常零命中） | 合法返回 `[]` + note，与 unavailable 严格区分 |
| quality insufficient | P2 不做质量判断（属 P14 Evaluation），仅在 doc 里声明非本期范围 |

实现为 `backend/app/tools/mcp_errors.py`：`MCPErrorCode(str, Enum)` + `MCPBoundaryError(Exception)`（携带 code + detail）。工具层把 boundary error 映射为各工具既有的 JSON 错误形状（如 interface_dict_tools 的 `{"error": ...}`）——**对外工具契约不变，对内不再静默**。Timeout/Retry 数值沿用 mcp_faq_client 配置模式（env 可配），统一 Retry=2 预算（宪法值），不做动态调整。

### 决策 5：测试钉子四件套（对应伞形 P2 验收「integration tests 完成」+ 用户诉求第 5 点）

1. **import boundary test**（contracts）：backend/app 下禁止 `from app.tools.rag_schema import` / `import rag_schema` / `interface_dict_tools` 出现在 tools 层之外；禁止任何文件出现 `D:/PyProject/ragent-py` 或 `import mcp_server`（防聪明 adapter 绕道）。
2. **tool allowlist test**（contracts）：registry 注册面 == 五个业务工具白名单；metadata.source 标注 `mcp`；出现 embedding/vector/rerank/chunk/ingest 字样的注册即红。
3. **schema contract test**（contracts）：mock MCP session 返回带内部字段的样本 → 断言规范化后只剩稳定字段；缺 text/score → 断言 MCP_INVALID_RESPONSE。
4. **failure semantics test**（graphs/smoke）：mock timeout/unavailable/invalid/empty 四情形 × flag on/off，断言 retry 次数、fallback 触发、显式错误路径——重点断言「unavailable ≠ 空数组」。

### 决策 6：职责划分声明（写进 mcp-contract.md，面试叙述锚点）

```text
RAG (ragent-py)：ingestion / chunking / embedding / retrieval / reranking / evidence retrieval
ReportAgent：requirement understanding / schema reasoning / SQL generation / SQL repair /
             report synthesis / user-facing orchestration
```

一句话：**RAG 是独立知识服务，ReportAgent 只消费 retrieval capability，不是把 RAG 当 Python library 嵌进来。**

### 与 P3 的职责切分（防重叠）

P2 只做「传输边界 + 工具契约 + 失败语义」；Context 组装（召回什么、注入哪段 prompt）、State 结构、memory 决策一律不碰——那是 P3 Context Runtime / P4 Memory 的地盘。具体红线：P2 不改 `_generate_sql` 的 prompt 注入结构（只改 search_faq 数据来源）、不改 data_graph 节点拓扑（只换 search_tables 底下那层）、不建 context/ 目录。

### 已识别的实现坑

- **Windows 子进程孤儿**：mcp_faq_client.close() 注释明确「不 close 则子进程孤儿化」——泛化 client 必须保持 lifespan 接线（main.py 已有 close_mcp_faq_client 调用点，改为关闭泛化 client 时同步兼容）。
- **monkeypatch 面**：interface_dict_tools 注释警告「不要改成 from httpx import post」——改造后测试 patch 点移到 mcp_client 层，旧 HTTP 测试（test_rag_schema / test_interface_dict_tools）改为打 fallback 路径或直接测 fallback 函数本体，不删覆盖。
- **_intent_analyze 共存**：sql_graph 物理上含 legacy intent 入口节点，search_faq 注入逻辑在 `_generate_sql`——改动只触及 `_generate_sql` 的数据来源，不动节点结构（P8 的活）。
- **token 缓存三份同格式**：ragent_token_cache / mcp_schema_server/token_cache / ragent-py 侧各一份——P2 不合并（跨仓库一致性已验证过），只在 mcp-contract.md 标注。
- **ragent-py MCP server 需 rag env python**：`RAGENT_MCP_PYTHON=D:/miniConda/envs/rag/python.exe`——泛化 client 沿用 env 配置模式，离线测试全部 mock session 不起真子进程。

---

## Files to change

### Task 0: 前置确认（只读，不改码）

- [ ] **Step 1**: `git show HEAD:D:/PyProject/ragent-py/mcp_server/server.py` 侧核对 `search_dictionary` 的入参出参（query/kb_ids/top_k → items[{text,score,title?}]）与本 plan 决策 3 表格一致；不一致则以实测为准回改本表格再开工。

### Task 1: mcp_errors.py + mcp_client.py（泛化 stdio client + 失败语义）

**Files:**
- Create: `backend/app/tools/mcp_errors.py`
- Create: `backend/app/tools/mcp_client.py`
- Test: `backend/tests/contracts/test_mcp_client.py`

- [ ] **Step 1: 写失败测试**——四类错误分类正确抛出（timeout→retry 2 次后 MCP_TIMEOUT；连接失败→MCP_UNAVAILABLE；非 JSON→MCP_INVALID_RESPONSE；正常空→返回 [] 不抛）；flag=PHASE2_MCP_ONLY=true 时 unavailable 不走 fallback 直接抛。测试全 mock（asyncio loop + session 打桩），不起真子进程。
- [ ] **Step 2: 实现 mcp_errors.py**：

```python
"""MCP 边界错误语义（P2，docs/plans/2026-08-26-p2-rag-mcp-boundary.md 决策 4）。

五分类中 quality-insufficient 属 P14，不在本枚举。Retry 预算固定 2（宪法值），
不做动态调整。"""
from __future__ import annotations

from enum import Enum


class MCPErrorCode(str, Enum):
    MCP_TIMEOUT = "MCP_TIMEOUT"
    MCP_UNAVAILABLE = "MCP_UNAVAILABLE"
    MCP_INVALID_RESPONSE = "MCP_INVALID_RESPONSE"


class MCPBoundaryError(RuntimeError):
    """MCP 边界失败——code 显式分类，禁止被吞成空数组。"""

    def __init__(self, code: MCPErrorCode, detail: str):
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
```

- [ ] **Step 3: 实现 mcp_client.py**——吸收 mcp_faq_client 的循环线程/会话/锁/close 模式，新增 `call_tool(name, args) -> list[dict]`（规范化：只留 text/score/title，剥内部字段）+ `PHASE2_MCP_ONLY` env 读取 + retry(2) + fallback 判定钩子 `def _fallback_allowed() -> bool`。`get_rag_mcp_client()` 进程级单例；`close_rag_mcp_client()` 幂等。faq 通道迁移到同一单例（`get_mcp_faq_client` 保留为兼容别名或薄封装，main.py lifespan 清理点不变行为）。
- [ ] **Step 4: pytest 全绿**（新增测试 + 既有 384 不回退）。
- [ ] **Step 5: Commit** — `feat(mcp): 泛化 stdio MCP client + 五分类失败语义（mcp_errors） + plan: p2-rag-mcp-boundary`

### Task 2: schema/字典/FAQ 三通道切换正路

**Files:**
- Modify: `backend/app/tools/rag_schema.py`、`backend/app/tools/interface_dict_tools.py`、`backend/app/tools/faq_tools.py`
- Modify: `backend/app/tools/data_tools.py`（docstring「ragent-py 字典库不可达时返回空数组」措辞改为反映新失败语义）
- Test: 更新 `backend/tests/smoke/test_rag_schema.py`、`backend/tests/contracts/test_interface_dict_tools.py`、`backend/tests/smoke/test_schema_faq.py`

- [x] **Step 1: 先改测试**——每个工具函数的测试加两组：mock MCP 成功路径（正路生效）+ mock MCP 失败 × flag 两态（fallback 生效/显式错误）。旧「不可达→空数组」断言改为「不可达+flag未锁→fallback 结果；不可达+flag锁定→JSON error 标记」。
- [x] **Step 2: 切实现**——三个文件的 `*_from_rag` / retrieve 调用改为：优先 `get_rag_mcp_client().call_tool("search_dictionary"/"search_faq", ...)`，捕获 `MCPBoundaryError` 且 `_fallback_allowed()` 时走原 httpx 直连函数（原地保留改名 `_retrieve_dict_http` 等），否则把 code/detail 写进工具既有错误形状。`_parse_table_doc` 保持单一副本（留在 rag_schema，mcp 通道返回同样 chunk 文本格式）。
- [x] **Step 3: data_tools docstring 措辞对齐**（search_tables/get_table_ddl/list_tables 的失败行）。
- [x] **Step 4: 全量回归 + 手工探活说明**——pytest 全量（445 passed，含新增 dispatcher 断言；P1 基线 384 不回退）；真实链路验证挂起至跑批窗口。
- [x] **Step 5: Commit** — `refactor(mcp): schema/字典/FAQ 正路切 MCP client，HTTP 直连降级 fallback + plan: p2-rag-mcp-boundary`

### Task 3: 四类测试钉子

**Files:**
- Create: `backend/tests/contracts/test_mcp_boundary_freeze.py`（import 断言 + allowlist）
- Create: `backend/tests/contracts/test_mcp_contract_schema.py`（决策 3 稳定/内部字段分离）
- Create: `backend/tests/graphs/test_mcp_failure_semantics.py`（四情形 × flag 两态矩阵；若纯工具层可测则放 contracts，以能离线为准）

- [ ] **Step 1: import boundary test**——扫描 backend/app：`app.tools.mcp_client` / `app.tools.mcp_errors` 的 import 只允许出现在 tools/ 包内；全文禁 `D:/PyProject/ragent-py`、`import mcp_server`、`from mcp_server`（除 mcp_faq_client 的 StdioServerParameters 配置项字符串）。red 验证：临时在 sql_graph 加 `from app.tools.rag_schema import x` → 红；撤 → 绿。
- [ ] **Step 2: tool allowlist test**——`register_all_tools()` 后 registry.all_tools().keys() == 白名单集（含既有 sql/report 工具全量枚举）；逐条断言 metadata 含 when_to_use 语义行（沿用 test_tool_descriptions 风格）；source ∈ {"local","mcp"} 且 schema 三工具 source=="mcp"。**（Step 2 review 第 1 轮修订：「schema 三工具」表述过宽——source 语义定为「实际正路 runtime 通道」，list_tables 正路 HTTP 直连必须 local；详见顶部落地记录 Task 3 Step 2 条目。）**
- [ ] **Step 3: schema contract test**——样本含 chunk_id/document_id/embedding 字段的 mock items → call_tool 规范化输出无这些键；text 缺失 → MCP_INVALID_RESPONSE。
- [ ] **Step 4: 全量回归 + Commit** — `test(contracts): MCP 边界四类钉子（import/allowlist/schema/failure） + plan: p2-rag-mcp-boundary`

### Task 4: docs/architecture/mcp-contract.md（第六份架构文档）

**Files:**
- Create: `docs/architecture/mcp-contract.md`

- [ ] **Step 1: 内容**（沿用五份文档双段结构：`> 状态: 冻结（P2，2026-08-26）` + 契约正文 + 现状映射表）——系统边界图（决策 1 ASCII 图）、工具白名单与禁入清单（决策 2）、I/O schema 稳定/内部字段表（决策 3）、failure 五分类表（决策 4）、RAG vs ReportAgent 职责划分（决策 6）、`PHASE2_MCP_ONLY` flag 语义与 Phase 5 收口预告、现状映射（四通道现状 → P2 后形态；mcp_schema_server 历史资产标注 P5 判定）。
- [ ] **Step 2: CLAUDE.md 宪法增量更新**——§7 Tool & MCP Contract 章现状行更新（「P2 已落地：正路 MCP、fallback 受 flag」），配套文档清单补第六份链接。其余章节不动。
- [ ] **Step 3: Commit** — `docs(architecture): mcp-contract 契约文档（第六份）+ CLAUDE.md §7 现状增量 + plan: p2-rag-mcp-boundary`

### Task 5: 索引登记 + P2 验收核对

**Files:**
- Modify: `docs/plans/README.md`、本 plan、伞形 plan §十八 P2 行

- [ ] **Step 1**: README 索引进行中区登记；完成后移已完成表。
- [ ] **Step 2**: 伞形 P2 验收清单逐项核对回填证据：[x] ReportAgent 不直接 import RAG [x] 所有 Schema Retrieval 走 MCP（正路语义，fallback 受 flag）[x] MCP input/output schema 固定 [x] timeout 可控 [x] failure 可识别 [x] integration tests 完成 [ ] fallback 最终关闭（← Phase 5，本 Phase 明确不打勾）。
- [ ] **Step 3: 最终全量回归**：backend pytest（基线不回退 + 新增钉子全过）+ frontend lint/test（应零变化）。
- [ ] **Step 4: Commit** — `docs(plans): P2 落地登记 + 验收核对 + plan: p2-rag-mcp-boundary`

### 挂起项（跑批窗口补做，如实记录）

- 真实跨进程验证：rag rag env 起 ragent-py + MCP server，ReportAgent 经泛化 client 走通 search_dictionary/search_faq 正路；kill ragent-py 验证 MCP_UNAVAILABLE → fallback / flag 锁定 → 显式错误。
- e2e（含 P1 Task 0 新断言）一并补跑。

---

## 复用现有工具

- **`backend/app/tools/mcp_faq_client.py`** —— stdio 会话生命周期全套（后台 loop 线程 / `_ensure_session` / `_call_lock` / `_reset` / `close` 幂等 / Windows 孤儿防护），Task 1 直接吸收其模式，不发明新传输层；
- **`backend/app/tools/ragent_token_cache.py`** —— 跨进程 token 缓存，fallback 路径继续用；
- **`backend/app/tools/rag_schema.py` 的 `_parse_table_doc` / `_build_ddl` / `_is_analytical_table`** —— chunk 渲染格式解析唯一副本保留原位；
- **`backend/tests/smoke/test_rag_schema.py` 的 mock HTTP 面** —— fallback 路径测试照此风格；
- **P1 钉子范式** —— `tests/contracts/test_legacy_import_freeze.py` 的「扫描 + 快照 + red 验证」三段式照搬为 import boundary test 骨架；
- **伞形 plan §八 Description 正反例** —— allowlist 测试断言语义来源。

## Verification

1. 每 Task 即时验证命令见各 Step；红线口径 = 基线不回退 + 新增测试全过 + P1 双侧 freeze 测试持续绿。
2. **red 验证强制**：Task 1（错误分类）与 Task 3（import 断言）各自做一次临时违规注入证明钉子扎人，流程同 P1 Task 3。
3. **failure 语义矩阵**：{TIMEOUT, UNAVAILABLE, INVALID_RESPONSE, EMPTY} × {flag off, flag on} 八格断言表全绿（EMPTY×两态都是合法 []，其余六格按决策 4 表现）。
4. **挂起**（服务未启动）：真实跨进程冒烟 + e2e，下次跑批窗口补做回填。

## 明确不做

- ❌ **不动 ragent-py 仓库**——MCP server 七个工具已够用；若 search_dictionary 出参缺 title 字段，在 ReportAgent 侧兼容，不改对方。
- ❌ **不删 httpx 直连代码**——那是 Phase 5 的收口动作；P2 只把它降级为 flag 管控的 fallback。
- ❌ **不处理 mcp_schema_server/**（本仓自建 server）——无消费者，历史资产；P5 判定去留，本 plan 仅文档标注。
- ❌ **不做 retrieval quality 判断**——P14 Evaluation 范围。
- ❌ **不建 context/ 目录、不改 prompt 注入结构、不改 graph 拓扑**——P3/P7/P8 地盘，P2 只换数据来源通道。
- ❌ **不合并三份 token cache**——跨仓库格式已验证，收益不抵风险，文档标注即可。
- ❌ **不做 ErrorEnvelope 全局统一**——本 plan 的 MCPErrorCode 是 P9 reliability/errors.py 的输入之一，不是它的完成；全局 envelope 归 P9。
- ❌ **不实跑真实链路**——服务停着，离线手段全覆盖；跨进程验证挂起至跑批窗口。
