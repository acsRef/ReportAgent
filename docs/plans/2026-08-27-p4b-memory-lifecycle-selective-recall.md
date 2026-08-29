# P4b 实施：Memory lifecycle + structured recall + SelectiveRecallPolicy

> **状态**: 已完成
> **上游**: [伞形 plan](2026-08-25-refactor-master-freeze.md) §六 / [memory-architecture.md](../architecture/memory-architecture.md) §二/§三/§五/§六/§七 / [P4a plan](2026-08-27-p4a-conversation-memory-decouple.md) 附录 A
> **接续**: P4a（分支 `p3` 续做，597 passed；conversation 已解耦，recall API 未动）
> **后续**: P4c（6 graph caller 翻转接入 ContextRuntime + assembler Filter/Budget + golden before/after）——见 §十四「未做」
> **落地日期**: 2026-08-27 → 2026-08-29

## Context（为什么做）

### 原始诉求
P4a 收口后，`context`/`memory` 边界清晰，但 Memory 仍是「全量召回 + 无 lifecycle + recall 返回拍平的 string」。P4b 要让 P3/P4 接口**形成完整闭环**（用户 P4b-review 清单）：
- Recall API 从 legacy string 平稳过渡到 structured records
- Memory write lifecycle 与 schema 扩展一致
- SelectiveRecallPolicy 兑现 `ContextDecisionPolicy` contract

### 现状问题（摸底，P4a 后）
1. `semantic_entry` 表列：`id/user_id/content/entry_type/memory_type/importance_score/intent_embedding/source/access_count/last_access_time/created_at`——**缺** §六 要求的 `scope/confidence/status/session_id/expires_at/updated_at`。
2. **违反 §五 line 49**：`memory/manager.remember_conversation_facts` 把 `compress_and_extract` 抽的 **LLM-inferred** 事实当 `insight` 写、且被 `UserMemory.search` 召回。契约要求「LLM inferred preference → 不进 active long-term memory」。
3. `MemoryManager.recall() -> str`（`memory_manager.py:38`）把 QueryMemory + UserMemory 的 structured 结果**拍平成 string**；底层 `UserMemory.search() -> list[RankedMemory]`、`QueryMemory.search_similar() -> list[dict]` 本就 structured。缺的是 domain 层 structured API。
4. `LegacyFallbackPolicy` 全开召回，`SelectiveRecallPolicy`（§二 四触发条件 + §三 agent 表）未实现。
5. 既有 `infra/memory/policy.py MemoryPolicy.extract_preference()`（正则检测「以后都用柱状图」等 explicit statement）——P4b write pipeline 复用，不重造。

### P4b 边界（5 领域）与 P4c 切分

| 领域 | P4b（本 plan） | P4c（后续） |
|---|---|---|
| 表 lifecycle 列 | ✅ migration 加列 + 回填 | — |
| write pipeline confidence/status 规则 | ✅ 实现（§五 固定规则） | — |
| recall 只返 active 非过期 | ✅ UserMemory/QueryMemory 过滤 | — |
| structured recall API | ✅ `recall_structured() -> list[RecallItem]` | — |
| RecallItem source 枚举 | ✅ 扩 memory_query/memory_semantic | — |
| ContextRuntime Step4 切 structured | ✅ | — |
| SelectiveRecallPolicy | ✅ 四触发条件 + agent 表分流（contract test 注入验证） | graph 真实接入 |
| 6 graph caller 翻转 | ❌ 保持 legacy `build_session_context` | ✅ P4c |
| assembler Filter/Conflict/Budget 真实装 | ❌ P4a 简化拼接保持 | ✅ P4c |
| golden before/after 对比 | ❌ | ✅ P4c |

## Design（做什么、模块怎么拼）

### 3.1 分层（承 P4a 依赖方向）

```text
app/memory/                    # domain/application 层
  ├── lifecycle.py             # MemoryStatus/MemoryScope/MemoryConfidence 枚举 + 规则常量
  ├── conversation.py          # (P4a) + write pipeline 改走 lifecycle
  ├── manager.py               # + remember_explicit_preference / remember_inferred_fact；recall_structured
  ├── semantic.py              # 新：收编 UserMemory 语义召回的 structured 视图（thin，委托 infra）
  └── query.py                 # 新：收编 QueryMemory 的 structured 视图（thin，委托 infra）

app/infra/memory/              # persistence 实现
  ├── user_memory.py           # search 加 status='active' AND 未过期过滤
  ├── query_memory.py          # 同上
  └── memory_manager.py        # + recall_structured() -> list[RecallItem]；旧 recall()->str 保留

app/context/                   # Runtime（P3）
  ├── decision.py              # + SelectiveRecallPolicy（§二四触发 + §三分流）；LegacyFallbackPolicy 保留
  └── runtime.py               # Step4 改调 recall_structured（RecallItem 不再 1:1 包 string）
```

> **P4b 语义边界**：semantic.py/query.py 是 **thin domain 视图**（委托 infra 的 UserMemory/QueryMemory，做 structured 映射 + 过滤语义），**不**搬迁 SQL 实现（那属更后期归位，P4b 明确不做以控范围）。

### 3.2 lifecycle 列 + migration（T1）

`memory.semantic_entry` 加列（幂等 `DO $$ IF NOT EXISTS`，沿用 init_pg.sql 既有模式）：
```sql
scope VARCHAR(16) DEFAULT 'user'
confidence VARCHAR(16) DEFAULT 'medium'
status VARCHAR(16) DEFAULT 'active'
session_id VARCHAR(64)          -- scope=session 时绑
expires_at TIMESTAMP            -- session/temporary 过期时刻
updated_at TIMESTAMP DEFAULT NOW()
```
**回填**（不破坏现网召回）：现有行 `status='active'`（保持当前可召回语义不回退）、`scope='user'`、`confidence = CASE WHEN importance_score>=0.7 THEN 'high' ELSE 'medium' END`。索引 `idx_semantic_entry_status_user ON (user_id, status)`。

`MemoryEntry`（infra `policy.py`）+ `UserMemory.save` 增 `scope/confidence/status/session_id/expires_at` 参数（默认 `status='active'/scope='user'/confidence='medium'` 保后向兼容）。

### 3.3 write pipeline（T2）：§五 固定规则

`memory/manager.py` 拆两个显式写入意图（**取代**现在单一 `remember_conversation_facts` 直写 insight）：
```python
async def remember_explicit_preference(user_id, text, *, source) -> int | None:
    # MemoryPolicy.extract_preference(text) 命中 → active stable_preference, confidence=high（§五）
    # 未命中 → None（不写）

async def remember_inferred_facts(user_id, facts, compressed_batch, *, session_id=None) -> None:
    # compress_and_extract / mem0 抽的 → status='candidate', confidence='low'（§五 line 49）
    # candidate 不被 recall 返出（T3 过滤）

async def remember_conversation_facts(user_id, updates, compressed_batch, *, session_id=None) -> None:
    # 兼容入口：schema/preference facts → remember_inferred_facts（保持 P4a conversation 调用签名）
```
`conversation.prepare_conversation_context` 仍调 `remember_conversation_facts`（时机不变，P4c/后期才移时机）——但其事实现在落 **candidate**（修正 §五 违规）。

**supersede（§六）**：`remember_explicit_preference` 写新 active stable 前，把同 `(user_id, memory_type='stable_preference', 同 key)` 旧 active → `status='superseded'`。V1 仅 explicit-statement stable 冲突处理，不做语义相似度 supersede。

### 3.4 recall 过滤（T3）

`UserMemory.search` / `get_user_preferences` / `QueryMemory.search_similar` SQL 加 `AND status='active' AND (expires_at IS NULL OR expires_at > NOW())`。默认过滤后 candidate/legacy-superseded/expired 不召回。**这修正 §五 违规**（LLM-inferred 现不再进召回结果）。

### 3.5 structured recall（T4）

```python
# app/context/assembler.py 扩 RecallItem（打破 P3 单 source 限制）
class RecallItem(TypedDict):
    raw_text: str
    source: Literal["legacy_memory_manager","memory_query","memory_semantic","memory_preference"]
    kind: str          # "query"/"semantic"/"preference"
    score: float       # 底层排序分（0~1）
    ref_id: int | None # semantic_entry.id / query_template.id

# app/infra/memory/memory_manager.py
async def recall_structured(query, user_id, *, top_k_queries=2, top_k_preferences=3) -> list[RecallItem]
    # QueryMemory.search_similar → RecallItem(kind='query', source='memory_query', score, ref_id)
    # UserMemory.search → kind by memory_type: stable/temporary→'preference'/'semantic'
# 旧 recall()->str 保留：内部改为 recall_structured 再 join，单点格式化（legacy parent_graph + facade 不破）
```
`ContextRuntime` Step4 改 `recall_items = await MemoryManager().recall_structured(...)`，**废弃** P3 的 `RecallItem(raw_text=text)` 1:1 包装。`recall()` 复用 `recall_structured()` → 无逻辑双写。

### 3.6 SelectiveRecallPolicy（T5）

```python
class SelectiveRecallPolicy:
    def decide(self, *, query, agent_policy, session_state) -> RecallDecision:
        # §二 四触发 → semantic/query/conversation bool；§三 agent 表封顶
        conv = _has_history_reference(query)                     # 触发1
        sem  = conv or _pref_affects(agent_policy, query) or _biz_def(query)  # 触发2,3
        q    = agent_policy != REPORT and _query_similar(query, session_state) # 触发4，Report ❌Query
        # §三 表：Requirement(conv✅ sem✅ q少量) / Execution(conv少量 sem✅ q✅) / Report(conv✅少量 sem✅ q❌)
        return RecallDecision(conversation=conv, semantic=sem, query=q, ...)
```
纯规则（无 LLM），关键词 + agent 表驱动。`ContextDecisionPolicy` Protocol 不变。graph 不接入（P4c）；**contract test 注入 `ContextRuntime(policy=SelectiveRecallPolicy())` 验证分流**（P3 已建此测试位）。

## Files to change

### 新增
- `backend/app/memory/lifecycle.py`（枚举 + 规则常量）
- `backend/app/memory/semantic.py` / `query.py`（thin structured 视图）
- migration SQL：`backend/scripts/init_pg.sql` 加列 + `backend/scripts/` 幂等段（沿用 DO $$ 模式）
- tests：`test_memory_lifecycle_contract.py` / `test_structured_recall_contract.py` / `test_selective_recall_contract.py` / `test_semantic_entry_migration.py`(persistence)

### 修改
- `infra/memory/user_memory.py`（save 新参 + search status 过滤 + RankedMemory 带 status/scope/confidence）
- `infra/memory/query_memory.py`（search_similar status 过滤）
- `infra/memory/memory_manager.py`（+ recall_structured；recall 委托）
- `memory/manager.py`（write pipeline 三函数 + supersede）
- `memory/conversation.py`（调用 remember_conversation_facts 现落 candidate）
- `context/assembler.py`（RecallItem 扩字段）
- `context/decision.py`（+ SelectiveRecallPolicy）
- `context/runtime.py`（Step4 切 recall_structured）
- `infra/memory/policy.py`（MemoryEntry 加 lifecycle 字段）

### 明确不改
- 6 处 graph `build_session_context` 调用（P4c 翻转）
- `LegacyFallbackPolicy` 行为（保留为默认 fallback）
- assembler 拼接逻辑（Filter/Budget 真实装留 P4c）
- `semantic_entry` 既有列语义（只加不删）

## Verification

- TDD：每领域先写 failing 钉子再实现
- `test_memory_lifecycle_contract.py`：枚举值 = §六 字面；RECALLABLE_STATUSES=={active}；confidence 规则常量固定
- `test_structured_recall_contract.py`：`recall_structured` 返 `list[RecallItem]` 带 kind/score/ref_id；`recall()` 仍 `-> str` 且 == `"\n".join(structured.raw_text)`；**P4a 钉子 `test_recall_structured_not_introduced_in_p4a` 反转删除**（P4b 正是引入处）
- `test_selective_recall_contract.py`：四触发条件各 1 正 1 负；§三 agent 表 3 行；Report→query False；candidate 注入 recall 不返
- **回归钉子**：现有 conversation 抽的 fact 写库后 `status='candidate'`（非 active），`UserMemory.search` 不返它——这是 §五 修正的行为变更，`test_build_session_context` L3 断言相应更新
- 全量离线 `pytest backend/tests/`：**597 不回退**（golden 走主 graph→facade→不触 recall，预期不动；status 过滤只影响经 recall 路径 = ContextRuntime/legacy，主链未接）
- persistence（DATABASE_URL）：migration 幂等跑两次不报错；旧行回填 active/user；新 candidate 行召回排除

## Explicitly NOT doing

| 不做 | 归属 | 理由 |
|---|---|---|
| 6 graph caller 翻转到 ContextRuntime.build | P4c | recall 过滤/selective 先经 contract test 验证再灰度接入 |
| assembler Filter/Conflict/Token Budget 真实装 | P4c | P4a 简化拼接保持 |
| golden before/after 全量对比 | P4c | 接入后才测端到端召回变化 |
| candidate→active promotion pipeline / evidence_count | 暂缓 | §五 V1 冻结「自动 promotion 不做」 |
| temporary_preference 物理迁 `agent.session` | 后期 | §五 line 50「第一版统一放 agent.session」；P4b 用 semantic_entry.scope='session' 满足**逻辑**分离，物理拆表待 Evaluation |
| UserMemory/QueryMemory SQL 实现搬 `app/memory/{semantic,query}.py` | P4c/后期 | P4b 只建 thin 视图，不搬持久化 |
| 语义相似度 supersede | 后期 | V1 仅同 key explicit-statement 冲突 |
| 改 LegacyFallbackPolicy 为 selective | P4c | 默认仍 fallback；selective 由 flag/接入决定 |
| State 单写者 enforcement | P8 | P3 附录 A 移出 |
| 触碰 legacy parent_graph `mm.recall()->str` | 不做 | CLAUDE.md §13；保留 recall() 正为此 |
| LLM 决定 confidence | 不做 | §五 规则固定 |
