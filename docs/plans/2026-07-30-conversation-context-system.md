# Plan: Conversation Context System — 分层对话上下文

## Context

The system currently has **no conversation history injection into LLM prompts**. Every LLM call is stateless: it receives only the current `user_query` + database schema, with no awareness of prior turns. Messages are persisted in `app.conversations` for UI display only — the LLM never reads them.

This causes a clear failure: if a user says "2024年华东销售趋势" then follows up with "再按产品细分", the second LLM call has no idea "华东" was ever discussed.

We need a multi-tier context system that:

1. Injects recent conversation history into LLM prompts as raw text
2. Compresses older history to avoid token bloat, using LLM summarization
3. Never loses precise business facts (field mappings, SQL logic, user preferences) to fuzzy summarization
4. Avoids compressing on every request (latency/cost)
5. Prevents summary bloat over time (A + batch → A' must be a rewrite, not an append)

Prior art in this repo:
- `app.conversations` stores all messages with `role`, `content`, `message_type`, `metadata`
- `agent.session` holds per-session state (phase, pointers to drafts/reports)
- `memory.semantic_entry` stores user preferences with pgvector embeddings, accessed via `UserMemory` with ranking (semantic × 0.6 + importance × 0.2 + frequency × 0.1 + recency × 0.1)
- `memory.query_template` stores SQL templates with pgvector
- `app/infra/memory/memory_manager.py` / `user_memory.py` / `policy.py` already exist but are not wired into the new graphs
- `app/llm.py` provides `call_llm()` with MiniMax client — reusable for compression calls
- `docs/memory-ranking-plan.md` documents the full ranking system design

## Design

### Architecture Overview

```
Request arrives → build_context()
                       │
          ┌────────────┼────────────────┐
          │            │                │
          ▼            ▼                ▼
   结构化数据         叙事摘要          最近对话
   (DB直读)          (L2 digest)       (L1 raw, last 10)
                        │
                  压缩时顺便提取（单次 LLM 调用输出 JSON）
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
   summary → L2 digest         extracted_schemas + extracted_preferences
   (覆盖写入)                        │
                                     ▼
                             memory.semantic_entry (L3)
                             通过 UserMemory.save() 写入
```

### Tier Definitions

| Tier | Storage | Content | Size Limit | Update Trigger |
|------|---------|---------|-----------|----------------|
| **L1** | `app.conversations` | Last N raw messages | 10 messages | Every user message |
| **L2** | `agent.session.digest` | Narrative summary (A + batch → A') | **800 chars** | Every `COMPRESS_BATCH` (10) messages beyond window |
| **L2.5** | `agent.session.mid_digest` | Archived L2 (long-term narrative arc) | 400 chars | Every 5 L2 rewrites |
| **L3** | `memory.semantic_entry` | Structured facts: field_mapping, calculation, preference | One row per fact | Extracted during compression |

### Key Constants

```python
RECENT_WINDOW = 10      # L1: keep last N messages as raw text
COMPRESS_BATCH = 10     # Compress every M messages beyond the window
L2_MAX_CHARS = 800      # Hard ceiling for L2 digest
L2_5_MAX_CHARS = 400    # Hard ceiling for L2.5 archive
L2_ARCHIVE_INTERVAL = 5 # Archive to L2.5 every N L2 rewrites
```

### Storage: 4 New Columns on `agent.session`

No new table — session-to-digest is 1:1. Adding to `agent.session` follows the existing pattern (`current_phase`, `latest_requirement_draft_id`, etc.):

```sql
ALTER TABLE agent.session
  ADD COLUMN IF NOT EXISTS digest TEXT,                  -- L2 narrative summary
  ADD COLUMN IF NOT EXISTS digest_msg_count INT DEFAULT 0, -- messages covered by digest
  ADD COLUMN IF NOT EXISTS digest_version INT DEFAULT 0,   -- rewrite counter
  ADD COLUMN IF NOT EXISTS mid_digest TEXT;               -- L2.5 long-term archive
```

### Incremental Merge Algorithm (Anti-Bloat)

**Core operation: replace, never append.** Each compression is a rewrite of the entire digest, constrained by a hard character limit.

```python
def build_context(session, messages, user_id) -> str:
    """Build LLM context string from conversation history."""
    total = len(messages)

    # 0-20 messages: no compression needed
    if total <= RECENT_WINDOW + COMPRESS_BATCH:
        return format_messages(messages)

    recent = messages[-RECENT_WINDOW:]
    old_count = total - RECENT_WINDOW

    # Compress if a new batch has accumulated
    if session.digest_msg_count < old_count:
        batch = messages[session.digest_msg_count:old_count]
        result = compress_and_extract(session.digest, batch)
        session.digest = result["summary"]          # ← OVERWRITE, not append
        session.digest_msg_count = old_count
        session.digest_version += 1

        # Archive to L2.5 every N rewrites
        if session.digest_version % L2_ARCHIVE_INTERVAL == 0:
            session.mid_digest = archive_to_l2_5(result["summary"])

        # Extract structured facts → L3
        for fact in result["extracted_schemas"]:
            UserMemory.save(user_id=user_id, content=fact, ...)
        for pref in result["extracted_preferences"]:
            UserMemory.save(user_id=user_id, content=pref, ...)

    # Assemble final context
    parts = []
    if session.mid_digest:
        parts.append(f"<长期脉络>\n{session.mid_digest}\n</长期脉络>")
    parts.append(f"<对话摘要>\n{session.digest}\n</对话摘要>")
    parts.append(f"<最新对话>\n{format_messages(recent)}\n</最新对话>")
    return "\n\n".join(parts)
```

### Duo-Channel Compression via Single LLM Call

The compression LLM call outputs a **JSON with three sections** — one for L2, two for L3:

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

    # Hard truncation safety net
    summary = (result.get("summary") or "")[:L2_MAX_CHARS]
    return {
        "summary": summary,
        "extracted_schemas": result.get("extracted_schemas", []),
        "extracted_preferences": result.get("extracted_preferences", []),
    }
```

### Compression Trigger Timeline

Example with `RECENT_WINDOW=10, COMPRESS_BATCH=10`:

| Total Messages | digest | digest_msg_count | digest_version | mid_digest | Compression Cost |
|---------------|--------|-----------------|----------------|------------|-----------------|
| 1-20 | NULL | 0 | 0 | NULL | None |
| 21 | A(compress 1-10) | 10 | 1 | NULL | 1 LLM call |
| 22-30 | A | 10 | 1 | NULL | None |
| 31 | A'(A + 11-20) | 20 | 2 | NULL | 1 LLM call |
| 41 | A''(A' + 21-30) | 30 | 3 | NULL | 1 LLM call |
| 51 | A'''(A'' + 31-40) | 40 | 4 | NULL | 1 LLM call |
| 61 | A''''(A''' + 41-50) | 50 | **5** | archive of A''' | 2 LLM calls (compress + archive) |

Compression runs at most once every 10 messages. L2.5 archive runs at most once every 50 messages.

### L3 Integration: Existing Infrastructure

The `memory.semantic_entry` table + `UserMemory` + `MemoryManager` already exist and handle:

- Dedup: same `user_id + content` → increment `access_count`
- Vector search: cosine similarity via pgvector
- Ranking: `semantic_similarity × 0.6 + importance × 0.2 + log(1+access_count) × 0.1 + recency × 0.1`
- Memory types: `stable_preference` (0.8), `temporary_preference` (0.5), `insight` (0.3)

No changes needed to the memory layer. The compression module calls `UserMemory.save()` for extracted facts.

### Context Injection Points

| File | Node | Inject? | Rationale |
|------|------|---------|-----------|
| `sql_graph.py` | `_plan` | Yes | Needs narrative context + field mappings |
| `sql_graph.py` | `_generate_sql` | Yes | Needs field mappings + preferences |
| `requirement_parser.py` | `parse_requirement` | Yes | Benefits from prior topic context |
| `report_graph.py` | `_plan_analysis` | No | Data-driven; conversation history not relevant |
| `security_guard.py` | `check` | No | Needs only the current query |
| `confirmed_execution_graph.py` | gate nodes | No | Gates check structural invariants, not conversation |

All injection happens via `build_context()` at the entry point of each LLM prompt template.

## Files to change

- **New** `backend/app/context.py` — `build_context()`, `compress_and_extract()`, `archive_to_l2_5()`
- `backend/app/infra/conversation/repository.py` — add `get_messages_up_to_count()` for efficient partial retrieval
- `backend/app/agent/sql_graph.py` — `_plan()` and `_generate_sql()` call `build_context()`, prepend to prompt
- `backend/app/agent/requirement_parser.py` — `parse_requirement()` call `build_context()`, prepend to prompt
- `backend/app/main.py` — wire `build_context()` into chat flow (pass `session_id` through graph state)
- `backend/scripts/init_pg.sql` — add `digest`, `digest_msg_count`, `digest_version`, `mid_digest` to `agent.session`

## Reused existing utilities

- `app/llm.py` `call_llm()` — reused for compression calls with cheaper model config
- `app/utils/text.py` `safe_json_parse()` — parse compression LLM JSON output
- `app/infra/memory/user_memory.py` `UserMemory.save()` — persist L3 extracted facts
- `app/infra/memory/memory_manager.py` `MemoryManager.recall()` — retrieve L3 facts during context building
- `app/infra/memory/policy.py` `MemoryPolicy.extract_preference()` — could supplement LLM-based extraction for known patterns
- `app/infra/conversation/repository.py` `get_messages()` — base retrieval (add count-limited variant)
- `app/infra/db/postgres.py` `get_pool()` — standard asyncpg pool access

## Verification

- **Unit: `backend/tests/test_context.py`** — new test file
  - `build_context()` with 5 messages → no compression, raw output
  - `build_context()` with 22 messages → first compression triggered, check digest written
  - `compress_and_extract()` with empty old digest → check JSON output shape
  - `compress_and_extract()` with existing digest → verify summary length ≤ 800 chars
  - L2.5 archive trigger at version 5
- **Unit: `backend/tests/graphs/test_sql_generation.py`** — add context injection assertions
  - Verify that `_plan()` prompt contains `<对话摘要>` and `<最新对话>` markers when messages > threshold
- **Integration: `REPORTAGENT_E2E=1 pytest backend/tests/e2e/test_full_flow.py -s`** — full flow with multi-turn
  - Two sequential queries → second query sees context from first
  - Verify `agent.session.digest` is populated after threshold crossed
- **Manual matrix:**
  | Scenario | Expected |
  |----------|----------|
  | 1 message | No digest, context = raw message only |
  | 15 messages | No digest (below compress threshold), context = all raw |
  | 22 messages | Digest created, context = digest + last 10 raw |
  | 35 messages | Digest overwritten (not appended), length ≤ 800 |
  | 65 messages (version=5) | L2.5 archived |
  | 2nd session | Separate digest, no cross-contamination |

## Explicitly NOT doing

- Adding a separate conversation_summary table (1:1 with session → just add columns)
- Migrating off MiniMax for compression (reuse existing `call_llm()`)
- Implementing L3 retrieval during context building (existing `MemoryManager.recall()` to be wired separately)
- Token-aware dynamic window sizing (hardcoded RECENT_WINDOW/COMPRESS_BATCH is sufficient for now)
- Cross-session digest merging (each session is independent)
- Frontend changes for context display (context is LLM-internal only)
- Changes to `app/llm.py` (context building is a separate concern)
