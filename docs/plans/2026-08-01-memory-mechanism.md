# Plan: 记忆机制完善——分层对话上下文 + mem0 事实抽取 + LFU/LRU 容量淘汰

> 状态: 已完成（5 轮共 23 测试绿；全套 146 passed + 启动冒烟；MEM0_ENABLED 默认关、降级纯 LLM 抽取）
>
> **基于（不推翻）**：[memory-ranking-plan.md](../memory-ranking-plan.md)（已实现的 pgvector 排序：语义 0.6 + 重要性 0.2 + LFU 0.1 + LRU 0.1）。
> **合并**：[2026-07-30-conversation-context-system.md](2026-07-30-conversation-context-system.md)（L1~L3 分层上下文，本 plan 落地它）。

## Context（背景）

现状盘点（重要，避免与既有决策打架）：

- **LFU/LRU 已存在**——作为 `UserMemory`/`QueryMemory` 召回打分的**排序因子**（`access_count`=LFU、`last_access_time`=LRU），语义主导。ranking-plan 刻意不做硬淘汰（纯 LFU/LRU 会让高频噪声压过语义相关性）。
- **mem0 曾被刻意替换**——ranking-plan 用 pgvector 排序替代了 mem0 直连；`app/memory.py`（mem0 封装）已成死代码（上一轮删除）。

本轮要补的（用户拍板）：

1. **分层对话上下文**（L1/L2/L2.5/L3）：目前每次 LLM 调用无状态，多轮对话不连贯。
2. **mem0 作 L3 事实抽取引擎**：压缩对话时用 mem0 自动抽取长期事实（字段映射/计算口径/偏好）写进 L3；`MEM0_ENABLED` 关闭时降级为纯 LLM 抽取。召回主路径仍是现有 pgvector 排序。
3. **LFU/LRU 容量上限淘汰**：在排序因子之上，给每用户记忆设条数上限，超出按 LFU/LRU 混合分（并保护高重要性项）真正删除最冷的，防止 `memory.semantic_entry` 无限增长。
4. **围绕长记忆的多轮测试**（用户强调）。

## Design（设计）

### 分层上下文（`app/context.py`，新建——深模块，窄接口）

```
L1  最近 RECENT_WINDOW(10) 条原始消息        来源 app.conversations
L2  叙事摘要 digest（≤800 字，覆盖重写防膨胀）  存 agent.session.digest
L2.5 长期归档 mid_digest（≤400 字）           存 agent.session.mid_digest，每 L2_ARCHIVE_INTERVAL(5) 次重写归档一次
L3  结构化事实（field_mapping/calculation/preference）  存 memory.semantic_entry
```

唯一入口 `build_context(messages, digest_state) -> (context_str, updates)`：
- `total ≤ RECENT_WINDOW + COMPRESS_BATCH(10)`：无需压缩，直接渲染 L1。
- 否则取最近 10 条作 L1；若 `digest_msg_count < total - 10`（新批次累积）触发一次压缩：`compress_and_extract(old_digest, batch)` **覆盖**旧摘要（绝不追加），顺带抽取 L3 事实；每 5 次重写归档 L2.5。
- 返回注入 prompt 的字符串（`<长期脉络>/<对话摘要>/<最新对话>`）+ 需回写 session 的 digest 字段更新。

`compress_and_extract` 封装单次 LLM 调用（便于 mock）；L3 抽取优先走 mem0（见下），失败/关闭走 LLM JSON 抽取。

### mem0 事实抽取引擎（`app/infra/memory/mem0_extractor.py`，新建）

恢复 mem0，但**只承担 L3 事实抽取**这一个角色（不做召回主路径）：

```python
MEM0_ENABLED = os.getenv("MEM0_ENABLED", "false") == "true"

async def extract_facts(text: str, user_id: str) -> list[str]:
    """用 mem0 从一段对话里抽取长期事实。mem0.add() 返回其抽取/更新的记忆条目。
    未启用或失败 → 返回 []，由调用方降级到 LLM 抽取。"""
```

- 懒加载 `mem0.Memory.from_config`（openai 兼容 LLM/embedder + chroma 本地向量库，配置同原 `app/memory.py`）。
- 失败/未启用一律 graceful 降级，绝不让记忆抽取拖垮主链路。

### 容量上限淘汰（`UserMemory` 增强）

- 常量 `USER_MEMORY_CAP`（默认 200/用户）。
- `save()` 后调用 `evict_over_capacity(user_id)`：超出上限时，按**淘汰分**升序删除最冷的若干条。
  - 淘汰分 = `log(1+access_count)×0.4 + recency×0.4 + importance×0.2`（**只用 LFU/LRU + 重要性，不含语义**——淘汰时无查询，且重要性保护稳定偏好不被误删）。
  - 删除得分最低（最冷、最不重要）的 `count - cap` 条。

### 会话 digest 持久化（`SessionManager` 增强 + schema）

- `agent.session` +4 列：`digest TEXT, digest_msg_count INT DEFAULT 0, digest_version INT DEFAULT 0, mid_digest TEXT`（`init_pg.sql` CREATE + 幂等 ALTER，并套用到现有 dev 库）。
- `SessionManager.get_context_state(session_id)` / `save_context_state(session_id, updates)`。

### 注入（低耦合 seam）

- `SQLAgentState` + `conversation_context: Optional[str]`；`_plan`/`_generate_sql` 在 prompt 前置 `state.get("conversation_context")`（有才加）。
- `requirement_parser.parse_requirement(..., conversation_context=None)` 前置到 prompt。
- 调用方（`confirmed_execution_graph._confirmed_sql_agent`、`requirement_analysis._requirement_parse`）调 `build_session_context(session_id, user_id)`（async glue：取消息+digest → build_context → 回写 digest → 存 L3）后传入。

## Files to change（文件改动）

| 操作 | 文件 | 说明 |
|---|---|---|
| CREATE | `app/context.py` | 分层上下文核心 + build_session_context glue |
| CREATE | `app/infra/memory/mem0_extractor.py` | mem0 L3 事实抽取（MEM0_ENABLED，graceful 降级） |
| MODIFY | `app/infra/memory/user_memory.py` | + USER_MEMORY_CAP + evict_over_capacity，save 后触发 |
| MODIFY | `app/infra/checkpoint/session.py` | + get_context_state / save_context_state |
| MODIFY | `scripts/init_pg.sql` | agent.session +4 列（CREATE + 幂等 ALTER） |
| MODIFY | `app/agent/sql_graph.py` | state 加 conversation_context；_plan/_generate_sql 前置 |
| MODIFY | `app/agent/requirement_parser.py` | parse_requirement 增 conversation_context 参数 |
| MODIFY | `app/agent/confirmed_execution_graph.py` / `requirement_analysis_graph.py` | 调用点 build_session_context 并传入 |
| MODIFY | `.env.example` | + MEM0_ENABLED 说明 |

## Reused existing utilities（复用工具）

- `app/infra/memory/user_memory.py` `UserMemory.save/search`（pgvector 排序）——L3 存储与召回主路径，不改其排序公式。
- `app/infra/memory/memory_manager.py` `MemoryManager`——召回编排不动。
- `app/infra/conversation/repository.py` `get_messages`——L1 消息来源（已按时间排序，Python 侧切片即可，不新增冗余函数）。
- `app/llm.py` `call_llm` + `app/utils/text.py` `safe_json_parse`——LLM 压缩/抽取降级路径。
- `app/embedding/service.py`——mem0 embedder 配置复用同款 SiliconFlow。
- 原 `app/memory.py` 的 mem0 config（git 历史）——抽取引擎配置蓝本。

## Verification（验证）——按用户要求分多轮

- **第 1 轮·上下文核心**（`tests/test_context.py`，smoke）：format_messages；build_context 无压缩路径；压缩触发；**摘要覆盖非追加**；L2.5 在第 5 次归档；800 字硬上限；抽取事实透传（mock call_llm）。
- **第 2 轮·mem0 抽取**（`tests/test_mem0_extractor.py`，smoke）：MEM0_ENABLED=false → 返回 []（降级）；mock mem0 客户端 → 抽出事实；mem0 抛错 → graceful 返回 []。
- **第 3 轮·容量淘汰**（`tests/persistence/test_user_memory_eviction.py`，persistence 真 PG）：写入超过 cap → 删最冷；高 importance 受保护不被删；淘汰分只含 LFU/LRU/重要性。
- **第 4 轮·digest 持久化**（`tests/persistence/test_session_context_state.py`，persistence）：save_context_state → get_context_state round-trip。
- **第 5 轮·glue + 注入**（`tests/test_build_session_context.py` + 图测试）：mock repo/session/memory，build_session_context 返回上下文并回写；sql_graph prompt 含 conversation_context。
- **回归**：全套 pytest 绿；后端启动冒烟（dev=MemorySaver 路径 + 新增列就绪）。

## Explicitly NOT doing（明确不做）

- **不用 mem0 做召回主路径**——召回仍是 pgvector 语义排序（尊重 ranking-plan 决策）；mem0 只做 L3 抽取。
- **不改 UserMemory/QueryMemory 的召回打分公式**（语义 0.6/0.5 主导）——只新增容量淘汰。
- **不做纯 LFU/LRU 硬淘汰替代排序**——淘汰只在超容量时触发，且保护重要性。
- **不做跨 session digest 合并**（每 session 独立）。
- **不做 token 感知动态窗口**（硬编码窗口足够）。
- **chroma/mem0 真实向量库的集成测试**——mem0 路径用 mock 测；真实 mem0 需 chromadb + LLM，属部署期验证（MEM0_ENABLED 默认 false）。
