# Plan: 分层对话上下文系统

> 状态: 暂缓（新特性，本轮不做）

## 背景

系统目前**完全没有对话历史注入到 LLM prompt 中**。每次 LLM 调用都是无状态的：只接收当前 `user_query` + 数据库 schema，对之前的轮次毫无感知。消息虽然持久化在 `app.conversations` 中供 UI 展示，但 LLM 从不读取。

这导致一个明确的失败场景：用户先说"2024年华东销售趋势"，然后补充"再按产品细分"——第二次 LLM 调用完全不知道"华东"曾经被讨论过。

我们需要一个多层上下文系统：

1. 将近期对话历史以原始文本形式注入 LLM prompt
2. 压缩较旧的历史以避免 token 膨胀，使用 LLM 摘要
3. 不丢失精确的业务事实（字段映射、SQL 逻辑、用户偏好）到模糊摘要中
4. 避免每次请求都压缩（延迟/成本）
5. 防止摘要随时间膨胀（旧摘要 + 新批次 → 新摘要必须是重写，而非追加）

现有相关基础设施：
- `app.conversations` 存储所有消息，包含 `role`、`content`、`message_type`、`metadata`
- `agent.session` 保存每个会话的状态（phase、指向草稿/报告的指针）
- `memory.semantic_entry` 通过 pgvector 存储用户偏好，通过 `UserMemory` 访问，评分公式为（语义相似度 × 0.6 + 重要性 × 0.2 + 频率 × 0.1 + 近因 × 0.1）
- `memory.query_template` 通过 pgvector 存储 SQL 模板
- `app/infra/memory/memory_manager.py` / `user_memory.py` / `policy.py` 已存在但未接入新图
- `app/llm.py` 提供 `call_llm()` 与 MiniMax 客户端——可复用用于压缩调用
- `docs/memory-ranking-plan.md` 记录了完整的 ranking 系统设计

## 设计

### 架构概览

```
请求到达 → build_context()
               │
    ┌───────────┼────────────────┐
    │           │                │
    ▼           ▼                ▼
 结构化数据     叙事摘要         最近对话
 (DB直读)      (L2 digest)      (L1 raw, last 10)
                 │
           压缩时顺便提取（单次 LLM 调用输出 JSON）
                 │
    ┌────────────┴─────────────┐
    │                          │
    ▼                          ▼
summary → L2 digest    extracted_schemas + extracted_preferences
(覆盖写入)                     │
                              ▼
                      memory.semantic_entry (L3)
                      通过 UserMemory.save() 写入
```

### 层级定义

| 层级 | 存储位置 | 内容 | 大小限制 | 更新时机 |
|------|---------|------|---------|----------|
| **L1** | `app.conversations` | 最近 N 条原始消息 | 10 条 | 每次用户消息 |
| **L2** | `agent.session.digest` | 叙事摘要（旧摘要 + 新批次 → 新摘要） | **800 字** | 每 `COMPRESS_BATCH`（10）条超出窗口的消息 |
| **L2.5** | `agent.session.mid_digest` | 归档的 L2（长期叙事脉络） | 400 字 | 每 5 次 L2 重写 |
| **L3** | `memory.semantic_entry` | 结构化事实：field_mapping、calculation、preference | 一行一个事实 | 压缩时提取 |

### 关键常量

```python
RECENT_WINDOW = 10      # L1：保留最近 N 条原始消息
COMPRESS_BATCH = 10     # 每 M 条超出窗口的消息压缩一次
L2_MAX_CHARS = 800      # L2 digest 硬上限
L2_5_MAX_CHARS = 400    # L2.5 归档硬上限
L2_ARCHIVE_INTERVAL = 5 # 每 N 次 L2 重写归档一次 L2.5
```

### 存储：`agent.session` 新增 4 列

不建新表——session 与 digest 是 1:1 关系。遵循现有模式（`current_phase`、`latest_requirement_draft_id` 等已有列）：

```sql
ALTER TABLE agent.session
  ADD COLUMN IF NOT EXISTS digest TEXT,                  -- L2 叙事摘要
  ADD COLUMN IF NOT EXISTS digest_msg_count INT DEFAULT 0, -- digest 覆盖的消息数
  ADD COLUMN IF NOT EXISTS digest_version INT DEFAULT 0,   -- 重写计数器
  ADD COLUMN IF NOT EXISTS mid_digest TEXT;               -- L2.5 长期归档
```

### 增量合并算法（防膨胀）

**核心操作：替换，绝不追加。** 每次压缩都是完整重写摘要，受硬性字符上限约束。

```python
def build_context(session, messages, user_id) -> str:
    """从对话历史构建 LLM 上下文字符串。"""
    total = len(messages)

    # 0-20 条消息：无需压缩
    if total <= RECENT_WINDOW + COMPRESS_BATCH:
        return format_messages(messages)

    recent = messages[-RECENT_WINDOW:]
    old_count = total - RECENT_WINDOW

    # 如果新批次已累积，触发压缩
    if session.digest_msg_count < old_count:
        batch = messages[session.digest_msg_count:old_count]
        result = compress_and_extract(session.digest, batch)
        session.digest = result["summary"]          # ← 覆盖，不是追加
        session.digest_msg_count = old_count
        session.digest_version += 1

        # 每 N 次重写归档 L2.5
        if session.digest_version % L2_ARCHIVE_INTERVAL == 0:
            session.mid_digest = archive_to_l2_5(result["summary"])

        # 提取结构化事实 → L3
        for fact in result["extracted_schemas"]:
            UserMemory.save(user_id=user_id, content=fact, ...)
        for pref in result["extracted_preferences"]:
            UserMemory.save(user_id=user_id, content=pref, ...)

    # 组装最终上下文
    parts = []
    if session.mid_digest:
        parts.append(f"<长期脉络>\n{session.mid_digest}\n</长期脉络>")
    parts.append(f"<对话摘要>\n{session.digest}\n</对话摘要>")
    parts.append(f"<最新对话>\n{format_messages(recent)}\n</最新对话>")
    return "\n\n".join(parts)
```

### 双通道压缩（单次 LLM 调用）

压缩 LLM 调用输出一个**包含三部分的 JSON**——一份用于 L2，两份用于 L3：

```python
def compress_and_extract(old_digest: str | None, batch_messages: list[dict]) -> dict:
    batch_text = format_messages(batch_messages)

    prompt = f"""分析旧摘要和最新对话，输出 JSON：

旧摘要（{len(old_digest or '')}字）：
{old_digest or '（无）'}

最新对话（{len(batch_messages)}条）：
{batch_text}

JSON：
{{
  "summary": "融合新旧信息的叙事摘要，不超过{L2_MAX_CHARS}字。不含具体字段名和数值。",
  "extracted_schemas": [
    {{"type": "field_mapping", "user_term": "销售额", "db_field": "sales_amount", "table": "fact_sales"}},
    {{"type": "calculation", "user_term": "环比", "sql_expression": "(value-LAG(value))/LAG(value)*100"}}
  ],
  "extracted_preferences": [
    "用户要求华东华南分开展示",
    "用户偏好柱状图"
  ]
}}

要求：
1. summary 是替换旧摘要，不是追加。严格不超过{L2_MAX_CHARS}字。
2. summary 只保留叙事脉络（话题切换、用户反馈、决策背景），不含字段名和数值。
3. extracted_schemas 只提取新出现或变更的字段映射/SQL逻辑。
4. extracted_preferences 只提取明确的用户偏好指令。"""

    raw = call_llm(prompt, model="MiniMax-M2.7-highspeed", max_tokens=1000)
    result = safe_json_parse(raw) or {}

    # 硬截断安全网
    summary = (result.get("summary") or "")[:L2_MAX_CHARS]
    return {
        "summary": summary,
        "extracted_schemas": result.get("extracted_schemas", []),
        "extracted_preferences": result.get("extracted_preferences", []),
    }
```

### 压缩触发时间线

以 `RECENT_WINDOW=10, COMPRESS_BATCH=10` 为例：

| 总消息数 | digest | digest_msg_count | digest_version | mid_digest | 压缩开销 |
|---------|--------|-----------------|----------------|------------|---------|
| 1-20 | NULL | 0 | 0 | NULL | 无 |
| 21 | A(compress 1-10) | 10 | 1 | NULL | 1 次 LLM 调用 |
| 22-30 | A | 10 | 1 | NULL | 无 |
| 31 | A'(A + 11-20) | 20 | 2 | NULL | 1 次 LLM 调用 |
| 41 | A''(A' + 21-30) | 30 | 3 | NULL | 1 次 LLM 调用 |
| 51 | A'''(A'' + 31-40) | 40 | 4 | NULL | 1 次 LLM 调用 |
| 61 | A''''(A''' + 41-50) | 50 | **5** | A''' 归档 | 2 次 LLM 调用（压缩+归档） |

压缩最多每 10 条消息运行一次。L2.5 归档最多每 50 条消息运行一次。

### L3 集成：现有基础设施

`memory.semantic_entry` 表 + `UserMemory` + `MemoryManager` 已存在并处理：

- 去重：相同 `user_id + content` → 递增 `access_count`
- 向量搜索：通过 pgvector 进行余弦相似度
- 排序：`semantic_similarity × 0.6 + importance × 0.2 + log(1+access_count) × 0.1 + recency × 0.1`
- 记忆类型：`stable_preference`（0.8）、`temporary_preference`（0.5）、`insight`（0.3）

memory 层不需要改动。压缩模块调用 `UserMemory.save()` 保存提取的事实。

### 上下文注入点

| 文件 | Node | 注入？ | 理由 |
|------|------|--------|------|
| `sql_graph.py` | `_plan` | 是 | 需要叙事上下文 + 字段映射 |
| `sql_graph.py` | `_generate_sql` | 是 | 需要字段映射 + 偏好 |
| `requirement_parser.py` | `parse_requirement` | 是 | 受益于先前话题上下文 |
| `report_graph.py` | `_plan_analysis` | 否 | 数据驱动；对话历史无关 |
| `security_guard.py` | `check` | 否 | 只需当前查询 |
| `confirmed_execution_graph.py` | gate nodes | 否 | gate 检查结构不变量，非对话 |

所有注入均通过 `build_context()` 在每个 LLM prompt 模板的入口点进行。

## 文件改动

- **新建** `backend/app/context.py` — `build_context()`、`compress_and_extract()`、`archive_to_l2_5()`
- `backend/app/infra/conversation/repository.py` — 新增 `get_messages_up_to_count()` 以高效部分检索
- `backend/app/agent/sql_graph.py` — `_plan()` 和 `_generate_sql()` 调用 `build_context()`，将结果前置到 prompt
- `backend/app/agent/requirement_parser.py` — `parse_requirement()` 调用 `build_context()`，将结果前置到 prompt
- `backend/app/main.py` — 将 `build_context()` 接入聊天流程（在 graph state 中传递 `session_id`）
- `backend/scripts/init_pg.sql` — 向 `agent.session` 中添加 `digest`、`digest_msg_count`、`digest_version`、`mid_digest`

## 复用工具

- `app/llm.py` `call_llm()` — 复用用于压缩调用，使用更便宜的模型配置
- `app/utils/text.py` `safe_json_parse()` — 解析压缩 LLM JSON 输出
- `app/infra/memory/user_memory.py` `UserMemory.save()` — 持久化 L3 提取的事实
- `app/infra/memory/memory_manager.py` `MemoryManager.recall()` — 在上下文构建时检索 L3 事实
- `app/infra/memory/policy.py` `MemoryPolicy.extract_preference()` — 可补充基于 LLM 的提取，用于已知模式
- `app/infra/conversation/repository.py` `get_messages()` — 基础检索（添加计数限制变体）
- `app/infra/db/postgres.py` `get_pool()` — 标准 asyncpg pool 访问

## 验证

- **单元测试：`backend/tests/test_context.py`** — 新测试文件
  - `build_context()` 5 条消息 → 无压缩，原始输出
  - `build_context()` 22 条消息 → 首次压缩触发，检查 digest 已写入
  - `compress_and_extract()` 空旧 digest → 检查 JSON 输出格式
  - `compress_and_extract()` 有现有 digest → 验证摘要长度 ≤ 800 字
  - L2.5 归档在 version=5 时触发
- **单元测试：`backend/tests/graphs/test_sql_generation.py`** — 添加上下文注入断言
  - 验证当消息超过阈值时，`_plan()` prompt 中包含 `<对话摘要>` 和 `<最新对话>` 标记
- **集成测试：`REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_full_flow.py -s`** — 多轮完整流程
  - 两次连续查询 → 第二次查询看到第一次的上下文
  - 验证超过阈值后 `agent.session.digest` 已填充
- **手动测试矩阵：**
  | 场景 | 预期 |
  |------|------|
  | 1 条消息 | 无 digest，context = 仅原始消息 |
  | 15 条消息 | 无 digest（低于压缩阈值），context = 所有原始消息 |
  | 22 条消息 | digest 已创建，context = digest + 最近 10 条原始消息 |
  | 35 条消息 | digest 已覆盖（非追加），长度 ≤ 800 |
  | 65 条消息（version=5） | L2.5 已归档 |
  | 第二个 session | 独立的 digest，无跨 session 污染 |

## 明确不做

- 新增单独的 conversation_summary 表（1:1 与 session → 直接加列即可）
- 从 MiniMax 迁移用于压缩（复用现有 `call_llm()`）
- 在上下文构建时实现 L3 检索（现有的 `MemoryManager.recall()` 将单独接入）
- Token 感知的动态窗口大小（硬编码 RECENT_WINDOW/COMPRESS_BATCH 目前足够）
- 跨 session digest 合并（每个 session 独立）
- 上下文的 UI 展示改动（context 仅在 LLM 内部使用）
- 对 `app/llm.py` 的改动（上下文构建是独立关注点）
