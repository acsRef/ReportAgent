# P4a 实施：Conversation Memory 解耦 + L3 write seam

> **状态**: 进行中
> **上游**: [伞形 plan](2026-08-25-refactor-master-freeze.md) §六 / [memory-architecture.md](../architecture/memory-architecture.md) §二/§四/§八/§九 / [P3 plan](2026-08-27-p3-context-runtime.md) 附录 A
> **接续**: P3（分支 `p3`，590 passed，checkpoint/state/context 骨架已落）
> **后续**: P4b（`recall_structured` + `semantic_entry` lifecycle 字段 + SelectiveRecallPolicy）
> **落地日期**: 2026-08-27

## Context（为什么做）

### 原始诉求
- CLAUDE.md §6 Memory Architecture 现状行：「context.py 四层底座 + infra/memory 在位；Selective Recall 决策与 semantic_entry 扩展 P4」。
- memory-architecture.md §八 Context Runtime 统一入口 + 目标目录（伞形 plan §二·二）：`backend/app/memory/`（conversation.py / semantic.py / query.py / policy.py / manager.py）是「长期保存什么」领域层，**尚未建立**。
- P3 review #9 决议：`_engine._save_l3_facts` 是 **Legacy Conversation/Memory Glue**——`context` 包直接 `from app.infra.memory import UserMemory / mem0_extractor`；P4 移除 context 包对 `infra.memory` 的直接依赖，Memory 写入归 Memory Manager。

### 现状问题（摸底）
1. `backend/app/context/_engine.py` 三类职责仍在 context 包内：Conversation Engine（L1/L2/L2.5）+ async glue（`_prepare_conversation_context`）+ **Memory 落库**（`_save_l3_facts` 直 import `infra.memory.UserMemory` + `mem0_extractor`）。context 包因此**违反 Forbidden Pattern「不让 Agent 直接访问 Memory DB——读写一律经 Memory Manager」**（P3 期以「兼容 glue」豁免，P4a 收口）。
2. `backend/app/memory/` 顶层目录不存在（伞形 plan §二·二 冻结路径，勿假设已建）。
3. `_prepare_conversation_context` 在**读上下文路径里顺手写 L3**——写入时机未走 Write After Reliable Event 显式环节。P4a **不**拆时机（属 P4b 表扩展后议题），只把写入实现从「context 直连 UserMemory」改为「context 经 memory/manager 领域函数」。

### P4a 边界（已与用户对齐，两个 grill 结论）

| grill 项 | 决议 |
|---|---|
| `app/memory/` vs `app/infra/memory/` 定位 | **A**：`app/memory/` = domain/application API（conversation.py / manager.py），`app/infra/memory/` = persistence implementation（UserMemory / QueryMemory / MemoryManager / mem0_extractor）。依赖方向：`app/memory` → `app/infra/memory`，反向禁止。 |
| `recall_structured` 是否进 P4a | **不进**（明确 NOT doing）。P4a 只解耦 Conversation Memory + L3 write seam；现有 `MemoryManager.recall() -> str` API **完全不变**。structured recall 归 P4b。 |

**P4a = 职责边界收敛，不启动 Selective Recall。**

## Design（做什么、模块怎么拼）

### 2.1 分层与依赖方向

```text
app/context/            # Agent 当前看到什么（Runtime 编排 + decision/policy/assembler）
  ├── _engine.py        # ← P4a：变薄 compatibility re-export facade（实现不再在此）
  ├── runtime.py        # Step 3 conversation import 改走 app.memory
  └── __init__.py       # facade：build_session_context 改走 app.memory；re-export 旧 API 不破

app/memory/             # ← P4a 新建：长期保存什么（domain/application 层）
  ├── conversation.py   # Conversation Memory 引擎（L1/L2/L2.5 + prepare + write seam 调用）
  └── manager.py        # remember_conversation_facts：mem0 增强 + 去重 + 委托 infra 写入

app/infra/memory/       # persistence implementation（P4a 不改）
  ├── memory_manager.py # MemoryManager.recall() -> str（**完全不变**）
  ├── user_memory.py    # UserMemory（**完全不变**，仍被 manager 调用）
  └── mem0_extractor.py # extract_facts（**完全不变**，改由 manager 调用）
```

**不变量**：
- `app/context` 不再 `from app.infra.memory import ...`（review #9 收口）。
- `app/memory` 可 `from app.infra.memory import ...`（domain → infra 合法方向）。
- `app/infra/memory` 不 import `app/context` 或 `app/memory`（防反向依赖）。

### 2.2 `app/memory/conversation.py`（迁入实现）

从 `context/_engine.py` **逐字节迁入**以下符号（P3 逻辑不动，只换 module 位置）：
- 常量：`RECENT_WINDOW / COMPRESS_BATCH / L2_MAX_CHARS / L2_5_MAX_CHARS / L2_ARCHIVE_INTERVAL`
- sync：`format_messages / format_context_block / archive_to_l2_5 / compress_and_extract / build_context`
- async：`prepare_conversation_context(session_id, user_id) -> str`（原 `_prepare_conversation_context`，**去 `_` 前缀**成 memory 域 public API；context 调它）

`prepare_conversation_context` 内部**不再**调 `_save_l3_facts`，改调 `from app.memory.manager import remember_conversation_facts`。

> `compress_and_extract` 仍 `from app.llm import call_llm`（宪法 §8 P6 前沿用 call_llm，不动）。

### 2.3 `app/memory/manager.py`（L3 write seam）

```python
async def remember_conversation_facts(
    user_id: int | str, updates: dict, compressed_batch: list[dict],
) -> None:
    """把压缩抽取的结构化事实写进 L3（memory.semantic_entry）。mem0 增强可选。
    P4a 从 context/_engine._save_l3_facts 迁入，唯一改动：
    - 不再 `from app.infra.memory import UserMemory` 直写；改 `MemoryManager().remember_preference(...)`
      （infra 既有方法，memory_type=insight / source=context_compress / importance=0.5 一致）
    - mem0 增强仍 `from app.infra.memory import mem0_extractor`（domain→infra 合法）
    保序去重 dict.fromkeys 不变。失败降级不拖垮主链路（逐条 try/except 保留）。
    """
```

**关键**：`remember_preference` 已存在于 `infra/memory/memory_manager.py:54`，P4a 复用它而非新增 infra 方法（复用即设计）。`importance=0.5` 需与旧 `_save_l3_facts` 的 `importance_score=0.5` 对齐——`remember_preference` 默认 `importance=0.3`，P4a 调用处显式传 `importance=0.5` 保 P3 `test_build_session_context` L3 断言不破。

### 2.4 `app/context/_engine.py`（compatibility re-export facade）

改为纯 re-export（**不**再含实现）：
```python
from app.memory.conversation import (
    RECENT_WINDOW, COMPRESS_BATCH, L2_MAX_CHARS, L2_5_MAX_CHARS, L2_ARCHIVE_INTERVAL,
    format_messages, format_context_block, archive_to_l2_5, compress_and_extract, build_context,
    prepare_conversation_context,
)
# P3 兼容别名（context 包私有名不破）
_prepare_conversation_context = prepare_conversation_context
__all__ = [...]
```

> **monkeypatch 陷阱（本 plan 一等公民）**：re-export 后 `compress_and_extract` 的实际 globals 在 `app.memory.conversation`。`monkeypatch.setattr(_engine, "compress_and_extract", X)` **不再影响** `build_context` 内的调用（它查 `conversation.__dict__`）。所有 P3 打 `_engine` 的 patch 必须改指 `app.memory.conversation`。见 §Verification。

### 2.5 `app/context/runtime.py` Step 3 改向

```python
# 旧：from app.context._engine import _prepare_conversation_context as _engine_prepare_...
# 新：from app.memory.conversation import prepare_conversation_context
conversation_context = await prepare_conversation_context(session_id, user_id)
```
ContextRuntime 的 recall step（Step 4）**不动**（仍 `MemoryManager().recall()`，P4b 才结构化）。

### 2.6 `app/context/__init__.py` facade

`build_session_context` 内部 import 改走 `app.memory.conversation.prepare_conversation_context`；re-export 旧 sync API 保持（`from app.context import build_context` 仍可用，可经 _engine 或直接）。

## Files to change

### 新增（3）
| 路径 | 用途 |
|---|---|
| `backend/app/memory/__init__.py` | 包标记；re-export `prepare_conversation_context / remember_conversation_facts` |
| `backend/app/memory/conversation.py` | Conversation Memory domain 层（L1/L2/L2.5 迁入 + `prepare_conversation_context`） |
| `backend/app/memory/manager.py` | `remember_conversation_facts` L3 write seam（mem0 + 委托 infra MemoryManager） |

### 修改（4）
| 路径 | 变更 |
|---|---|
| `backend/app/context/_engine.py` | 实现 → compatibility re-export facade（from app.memory.conversation） |
| `backend/app/context/runtime.py` | Step 3 import 改走 `app.memory.conversation.prepare_conversation_context`；recall Step 4 不动 |
| `backend/app/context/__init__.py` | `build_session_context` 改走 `app.memory`；旧 API re-export 保留 |
| `backend/tests/contracts/test_legacy_import_freeze.py` | 若 LEGACY BRIDGE 快照含 context→infra.memory，P4a 后 context 不再 import infra.memory——**核对不误伤**（此断言是「新代码禁止 import legacy」，与 memory 无关，预期不改；列出以防漏） |

### 测试同步改（3，属 Implementation 不属新增行为）
| 路径 | 变更 |
|---|---|
| `backend/tests/test_context.py` | `monkeypatch.setattr(_engine, "compress_and_extract"/"call_llm", ...)` → 打 `app.memory.conversation` |
| `backend/tests/test_build_session_context.py` | 同上；L3 断言（importance=0.5）保持通过 |
| `backend/tests/contracts/test_context_runtime_contract.py` | patch target `app.context.runtime._engine_prepare_conversation_context` → `app.context.runtime.prepare_conversation_context`（随 runtime import 改名） |
| `backend/tests/contracts/test_context_package_facade.py` | `test_async_glue_moved_to_engine` 语义更新（_engine 现为 re-export）；新增「context 包不 import infra.memory」钉子 |

### 不变（P4b/P4c 边界）
- `MemoryManager.recall() -> str`（现有 string API 完全不变）
- `infra/memory/user_memory.py / query_memory.py / memory_manager.py / mem0_extractor.py`（persistence 层不动）
- `RecallItem`（仍 P3 最小包装，P4b 才枚举化 source）
- `semantic_entry` 表结构（P4b 才加 lifecycle 字段）
- 所有 graph 节点（`build_session_context` 兼容路径不变）

## Reused existing utilities（复用即设计）

| 复用对象 | 路径 | 方式 |
|---|---|---|
| L1/L2/L2.5 引擎全部函数 | `context/_engine.py` → 迁入 `memory/conversation.py` | 逻辑逐字节搬，**不重写** |
| `MemoryManager.remember_preference()` | `infra/memory/memory_manager.py:54` | manager.remember_conversation_facts 委托它写单条 fact，复用即设计（不新增 infra 方法） |
| `mem0_extractor.extract_facts()` | `infra/memory/mem0_extractor.py` | 改由 `app/memory/manager.py` 调用（domain→infra） |
| `session_manager.get_context_state/save_context_state` | `infra/checkpoint/session.py` | `prepare_conversation_context` 沿用（import 路径不变） |
| `infra.conversation.repository.get_messages` | `infra/conversation/repository.py` | 沿用 |
| `call_llm / safe_json_parse` | `app.llm` / `app.utils.text` | `compress_and_extract` 沿用 |

## Verification（端到端验证）

### 单元 / contract（不需 DATABASE_URL）
```bash
cd backend
pytest tests/test_context.py -v                        # monkeypatch target 改后应全绿
pytest tests/test_build_session_context.py -v          # L3 importance=0.5 断言不破
pytest tests/contracts/test_context_package_facade.py -v
pytest tests/contracts/test_context_runtime_contract.py -v
```

### 新增钉子（P4a 专属，TDD 先红后绿）
`tests/contracts/test_memory_conversation_decouple.py`：
1. `app.memory.conversation` 暴露 `build_context / prepare_conversation_context`（domain 层存在性）
2. `app.memory.manager.remember_conversation_facts` 存在且委托 `MemoryManager.remember_preference`（patch 计数）
3. **review #9 核心钉子**：AST 扫 `app/context/**/*.py`，断言无任何 `from app.infra.memory` / `import app.infra.memory`（context 包与 infra.memory 解耦）
4. **反向依赖钉子**：AST 扫 `app/infra/memory/**/*.py`，断言无 `import app.memory` / `import app.context`（persistence 不依赖 domain）
5. `recall_structured` **不存在**（P4a 明确 NOT doing 钉子：`assert not hasattr(MemoryManager, "recall_structured")`）
6. `MemoryManager.recall() -> str` 签名不变（返回类型 str）

### 全量离线（CLAUDE.md §15 红线）
```bash
cd backend && pytest          # 590 不回退；P4a 新增 ~6 钉子 → ~596
```

### 冒烟矩阵（P4a 4 项）
1. **行为等价**：`build_session_context(session_id, user_id)` 输出与 P3 逐字相等（同 mock messages/digest）——证明搬家不改语义
2. **解耦生效**：`import app.context` 后 `sys.modules` 不含 `app.infra.memory.user_memory`（除非显式触发压缩；静态面 context 无 infra.memory import）
3. **recall API 不变**：`MemoryManager.recall` 仍是 `-> str`；`test_context_runtime_contract.py` 全绿（RecallItem 仍 1:1 包装）
4. **legacy import freeze 不误伤**：`test_legacy_import_freeze.py` 仍 0 命中

### Golden Set
```bash
pytest backend/tests/golden/   # 16/0/4 不回退（P4a 不改召回行为，预期不动）
```

## Explicitly NOT doing（P4a 反向 scope）

| 不做 | 归属 | 理由 |
|---|---|---|
| `MemoryManager.recall_structured()` / 改 recall 签名 | **P4b** | 用户 grill ② 决议：P4a 保持 recall API 完全不变，不扩成「重做 Recall」 |
| `RecallItem.source` 枚举化（memory_query/semantic/conversation） | P4b | 同上，随 structured recall 一起做 |
| `semantic_entry` 表 lifecycle 字段（scope/confidence/status/session_id/expires_at/updated_at） | P4b | 表扩展 + write pipeline 是 P4b 主题 |
| L3 写入时机从读路径移到显式 reliable event 环节 | P4b | P4a 只改「谁写」（manager 而非 context 直连），不改「何时写」；时机拆分依赖 P4b 表状态字段 |
| SelectiveRecallPolicy（四触发条件） | P4c | 伞形 plan §六 P4 后半 |
| graph 节点接入 ContextRuntime.build（6 处迁移） | P4c | 同上 |
| assembler Filter/Conflict/Budget 真实装 | P4c | 同上 |
| 迁移 `infra/memory/{user_memory,query_memory}.py` → `app/memory/{semantic,query}.py` | P4b/P4c | P4a 只建 conversation.py + manager.py；semantic/query 文件归位在后续 phase（避免 drive-by 大搬） |
| State 单写者 enforcement | P8 | P3 附录 A 决议移出 |
| 触碰 `infra/memory/*` 实现 | 不做 | P4a 仅**调用**其既有方法 |
| 触碰 legacy（parent_graph.py 仍用 `mm.recall()->str`） | 不做 | CLAUDE.md §13；legacy recall 调用方保持 string API 正是不改 recall 的理由 |
| 动 `compress_and_extract` 的 `call_llm` 调用 | P6 | 宪法 §8 Unified LLM Adapter 收口 |
| mem0 开关/逻辑改动 | P14 | Evaluation 数据决定去留 |
| 调整 Forbidden Patterns | 不做 | CLAUDE.md §2 冻结 |

## 附录 A：P4a 完成后 P4b 的接口预留

```text
P4a（本 plan）
├── app/memory/conversation.py         ← conversation 引擎安家
├── app/memory/manager.remember_conversation_facts  ← L3 write seam（当前写 insight）
├── context 包零 infra.memory 依赖      ← review #9 收口
└── recall API：MemoryManager.recall() -> str（未动）

P4b（下一 plan，接口衔接点）
├── app/memory/manager.recall_structured() -> list[RecallItem]  ← 与 remember_* 同域
├── semantic_entry lifecycle 字段
├── remember_conversation_facts 内按 confidence 规则赋 status/scope  ← write pipeline
└── RecallItem.source 枚举 + ContextRuntime Step4 切换
```

`remember_conversation_facts` 是 P4b write pipeline 的**占位 seam**：现在只写 insight，P4b 在同一函数内接入 confidence 规则 + lifecycle，不再动 conversation.py。
