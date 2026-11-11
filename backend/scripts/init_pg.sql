-- ReportAgent PostgreSQL Schema Initialization
-- Run: psql -U ragent -d ragent -f init_pg.sql
-- This file is idempotent; safe to re-run.

-- ============================================================
-- pgvector（memory.query_template.intent_embedding 等 VECTOR 列依赖；
-- CI 全新 pgvector 容器首跑必须有 extension——2026-09-04 CI 首绿暴露）
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Schemas
-- ============================================================
CREATE SCHEMA IF NOT EXISTS agent;
CREATE SCHEMA IF NOT EXISTS memory;
CREATE SCHEMA IF NOT EXISTS observability;
CREATE SCHEMA IF NOT EXISTS app;

-- ============================================================
-- app schema (auth + conversation)  -- must precede agent.session
-- because agent.session.user_id is INT REFERENCES app.users(id).
-- ============================================================

CREATE TABLE IF NOT EXISTS app.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app.conversations (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    user_id INT NOT NULL REFERENCES app.users(id),
    role VARCHAR(16) NOT NULL,
    content TEXT,
    message_type VARCHAR(32) DEFAULT 'text',
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_user_session ON app.conversations (user_id, session_id, created_at);

-- ============================================================
-- agent schema
-- ============================================================

CREATE TABLE IF NOT EXISTS agent.session (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(64) UNIQUE NOT NULL,
    user_id INT,  -- nullable: anonymous before login; INT to FK app.users
    title VARCHAR(256),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_checkpoint_at TIMESTAMP,  -- nullable: LangGraph checkpoint may not exist yet
    status VARCHAR(32) DEFAULT 'active'
);

-- Soft-migrate: if a legacy VARCHAR(64) user_id column exists from an older
-- init, null out garbage values then ALTER it to INT. Garbage here is any
-- value that is not a pure integer (e.g. legacy code wrote session_id strings
-- into user_id, which the Phase 2 fix in main.py prevents going forward).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'agent'
          AND table_name = 'session'
          AND column_name = 'user_id'
          AND data_type = 'character varying'
    ) THEN
        UPDATE agent.session SET user_id = NULL WHERE user_id !~ '^[0-9]+$';
        ALTER TABLE agent.session
            ALTER COLUMN user_id TYPE INT USING NULLIF(user_id, '')::INT;
    END IF;
END $$;

-- ============================================================
-- memory schema
-- ============================================================

CREATE TABLE IF NOT EXISTS memory.query_template (
    id SERIAL PRIMARY KEY,
    user_id INT,
    intent_embedding VECTOR(1536),
    question TEXT NOT NULL,
    sql_text TEXT NOT NULL,
    schema_context JSONB,
    target_metric VARCHAR(128),
    success_count INT DEFAULT 1,
    failure_count INT DEFAULT 0,
    access_count INT DEFAULT 0,
    verified BOOLEAN DEFAULT FALSE,
    last_used_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_query_template_embedding
    ON memory.query_template
    USING ivfflat (intent_embedding vector_cosine_ops)
    WITH (lists = 100);

-- Soft-migrate query_template.user_id (A-4): 旧库没有该列时补上。
-- 历史行保持 NULL——安全优先：NULL 行不再被任何用户召回。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'memory'
          AND table_name = 'query_template'
          AND column_name = 'user_id'
    ) THEN
        ALTER TABLE memory.query_template ADD COLUMN user_id INT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_query_template_user ON memory.query_template (user_id);

-- Query Memory 的**失败**侧（memory-architecture §四）：失败 SQL 不进入成功经验，
-- 但必须留下明细（failure_category / failed_sql / retry_count / error），否则
-- 「那次为什么失败」只能翻日志。2026-11-09（plan memory-system-conformance D2）：
-- 此前 contract 写了这四个字段，表里一个都没有，record_failure 只能对已存在的
-- 成功行 +1——失败根本无处可落，故单开一张表，不把 query_template 继续撑宽。
CREATE TABLE IF NOT EXISTS memory.query_failure (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    session_id VARCHAR(64),
    question TEXT NOT NULL,
    failed_sql TEXT,
    failure_category VARCHAR(64),
    error_message TEXT,
    retry_count INT DEFAULT 0,
    target_metric VARCHAR(128),
    trace_id VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_query_failure_user_time
    ON memory.query_failure (user_id, created_at DESC);

-- 离线固化（plan 2026-11-09-memory-admission-and-consolidation）：
-- `semantic_entry.promoted_at`（候选已固化标记）与 `evidence_count`（写入侧证据）
-- 已经写进下面的 CREATE TABLE（全新库靠它），老库走紧随其后的软迁移。
-- 顺序提醒：ALTER 必须**在 CREATE 之后**，否则全新库会先报「relation does not exist」
-- （2026-11-10 CI 因此红过一次）。

-- ② 每个用户一个游标：只用于「上次成功跑到什么时候」，供运维判断批处理落后多久。
CREATE TABLE IF NOT EXISTS memory.consolidation_cursor (
    user_id INT PRIMARY KEY,
    last_window_end TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ③ 每轮记账：晋级/合并/复审/过期/收敛各多少、是否 dry-run、有没有报错。
-- merged / reviewed 是 2026-11-10（plan semantic-memory-merge）加的：归并判定要能看出
-- 「这轮累加了几条证据」「几对进了灰区复审」，否则批处理跑了什么完全不可见。
CREATE TABLE IF NOT EXISTS memory.consolidation_run (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    started_at TIMESTAMP DEFAULT NOW(),
    finished_at TIMESTAMP,
    considered INT DEFAULT 0,
    promoted INT DEFAULT 0,
    expired INT DEFAULT 0,
    superseded INT DEFAULT 0,
    skipped INT DEFAULT 0,
    errors INT DEFAULT 0,
    dry_run BOOLEAN DEFAULT FALSE,
    note TEXT,
    merged INT DEFAULT 0,
    reviewed INT DEFAULT 0
);

-- 软迁移：老库没有这两列时补上（必须在本表 CREATE 之后）
ALTER TABLE memory.consolidation_run ADD COLUMN IF NOT EXISTS merged INT DEFAULT 0;
ALTER TABLE memory.consolidation_run ADD COLUMN IF NOT EXISTS reviewed INT DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_consolidation_run_user_time
    ON memory.consolidation_run (user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS memory.semantic_entry (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    content TEXT NOT NULL,
    entry_type VARCHAR(32) DEFAULT 'semantic',
    memory_type VARCHAR(32) DEFAULT 'insight',
    importance_score REAL DEFAULT 0.0,
    intent_embedding VECTOR(1536),
    source VARCHAR(64),
    access_count INT DEFAULT 0,
    last_access_time TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW(),
    -- P4b lifecycle (memory-architecture §六): scope/confidence/status/session_id/expires_at/updated_at
    scope VARCHAR(16) DEFAULT 'user',
    confidence VARCHAR(16) DEFAULT 'medium',
    status VARCHAR(16) DEFAULT 'active',
    session_id VARCHAR(64),
    expires_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW(),
    -- 2026-11-09/10（plan long-term-memory-table / semantic-memory-merge）：
    -- promoted_at = 候选已固化（候选池 = status='candidate' AND promoted_at IS NULL）；
    -- evidence_count = 写入侧证据（同一说法被观察到几次），与读侧热度 access_count 分开。
    -- 必须写在 CREATE 里：全新库只跑这一条语句。
    promoted_at TIMESTAMP,
    evidence_count INT DEFAULT 1
);

-- 软迁移（老库补列；**必须在上一句 CREATE 之后**，否则全新库先报「表不存在」）
ALTER TABLE memory.semantic_entry ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMP;
ALTER TABLE memory.semantic_entry ADD COLUMN IF NOT EXISTS evidence_count INT DEFAULT 1;

-- Soft-migrate semantic_entry.user_id from VARCHAR(128) to INT.
-- Garbage rows (non-numeric user_id) are deleted; the column is NOT NULL,
-- so a DELETE is cleaner than a NULL cast here.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'memory'
          AND table_name = 'semantic_entry'
          AND column_name = 'user_id'
          AND data_type = 'character varying'
    ) THEN
        DELETE FROM memory.semantic_entry WHERE user_id !~ '^[0-9]+$';
        ALTER TABLE memory.semantic_entry
            ALTER COLUMN user_id TYPE INT USING NULLIF(user_id, '')::INT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_semantic_entry_user ON memory.semantic_entry (user_id);

-- P4b soft-migrate: add lifecycle columns to existing semantic_entry (idempotent).
-- Fresh installs already have them via CREATE TABLE above; this covers older DBs.
-- Backfill: existing rows keep current recallable semantics → status='active',
-- scope='user', confidence derived from importance (>=0.7 high else medium).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='scope') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN scope VARCHAR(16) DEFAULT 'user';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='confidence') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN confidence VARCHAR(16) DEFAULT 'medium';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='status') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN status VARCHAR(16) DEFAULT 'active';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='session_id') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN session_id VARCHAR(64);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='expires_at') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN expires_at TIMESTAMP;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='memory' AND table_name='semantic_entry' AND column_name='updated_at') THEN
        ALTER TABLE memory.semantic_entry ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_semantic_entry_status_user ON memory.semantic_entry (user_id, status);

-- ============================================================
-- observability schema
-- ============================================================

CREATE TABLE IF NOT EXISTS observability.agent_trace (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) UNIQUE NOT NULL,
    session_id VARCHAR(64),
    user_id INT,
    user_query TEXT,
    status VARCHAR(32),
    start_time TIMESTAMP DEFAULT NOW(),
    end_time TIMESTAMP,
    total_duration_ms INT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_trace_session ON observability.agent_trace (session_id);
CREATE INDEX IF NOT EXISTS idx_agent_trace_trace_id ON observability.agent_trace (trace_id);

-- Soft-migrate agent_trace.user_id (A-3): 旧库没有该列时补上。
-- 历史无主行保持 NULL——审计数据、非业务数据，对所有人不可见（安全优先）。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'observability'
          AND table_name = 'agent_trace'
          AND column_name = 'user_id'
    ) THEN
        ALTER TABLE observability.agent_trace ADD COLUMN user_id INT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_agent_trace_user ON observability.agent_trace (user_id);

CREATE TABLE IF NOT EXISTS observability.agent_trace_span (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    parent_span_id VARCHAR(64),
    span_id VARCHAR(64) NOT NULL,
    span_name VARCHAR(128),
    span_type VARCHAR(32),
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_ms INT,
    status VARCHAR(32),
    input JSONB,
    output JSONB,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_span_trace_id ON observability.agent_trace_span (trace_id);
CREATE INDEX IF NOT EXISTS idx_span_span_id ON observability.agent_trace_span (span_id);

CREATE TABLE IF NOT EXISTS observability.llm_call (
    id BIGSERIAL PRIMARY KEY,
    span_id VARCHAR(64) NOT NULL,
    model VARCHAR(64),
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    latency_ms INT,
    cost NUMERIC(10, 6) DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_llm_call_span_id ON observability.llm_call (span_id);

-- ============================================================
-- Phase 2 / Phase 4 of the conversational workbench plan:
-- requirement_draft / report_version / report_template
-- Source of truth: docs/persistence.md
-- ============================================================

-- agent.requirement_draft
CREATE TABLE IF NOT EXISTS agent.requirement_draft (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    user_id INT NOT NULL REFERENCES app.users(id),
    version INT NOT NULL,
    user_query TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    confirmed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(session_id, version)
);

CREATE INDEX IF NOT EXISTS idx_requirement_draft_session
    ON agent.requirement_draft(session_id, version DESC);

-- agent.report_version
CREATE TABLE IF NOT EXISTS agent.report_version (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    user_id INT NOT NULL REFERENCES app.users(id),
    version INT NOT NULL,
    parent_version INT,
    requirement_draft_id BIGINT REFERENCES agent.requirement_draft(id),
    adjustment_text TEXT,
    title TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    report_payload JSONB NOT NULL,
    query_snapshot JSONB,
    trace_id VARCHAR(64),
    favorite BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(session_id, version)
);

CREATE INDEX IF NOT EXISTS idx_report_version_session
    ON agent.report_version(session_id, version DESC);

CREATE INDEX IF NOT EXISTS idx_report_version_user_session
    ON agent.report_version(user_id, session_id);

-- app.report_template
CREATE TABLE IF NOT EXISTS app.report_template (
    id BIGSERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES app.users(id),
    name VARCHAR(128) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    requirement_payload JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, name)
);

-- agent.session extensions for Phase 4 (current_phase, pointers)
ALTER TABLE agent.session
    ADD COLUMN IF NOT EXISTS latest_requirement_draft_id BIGINT,
    ADD COLUMN IF NOT EXISTS latest_report_version INT,
    ADD COLUMN IF NOT EXISTS current_phase VARCHAR(32) NOT NULL DEFAULT 'idle',
    ADD COLUMN IF NOT EXISTS last_failed_action VARCHAR(32);

-- agent.session extensions for layered conversation context (L2 摘要 / L2.5 归档)
-- 见 docs/plans/2026-08-01-memory-mechanism.md。digest 为覆盖重写的叙事摘要，
-- digest_msg_count 记录已压缩到的消息数，digest_version 为重写计数（每 N 次归档 L2.5）。
ALTER TABLE agent.session
    ADD COLUMN IF NOT EXISTS digest TEXT,
    ADD COLUMN IF NOT EXISTS digest_msg_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS digest_version INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS mid_digest TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_session_latest_requirement'
          AND table_schema = 'agent'
          AND table_name = 'session'
    ) THEN
        ALTER TABLE agent.session
            ADD CONSTRAINT fk_session_latest_requirement
            FOREIGN KEY (latest_requirement_draft_id)
            REFERENCES agent.requirement_draft(id);
    END IF;
END $$;


-- 长期记忆独立表（plan 2026-11-09-long-term-memory-table）。
-- 为什么要单独一张：semantic_entry 混着「候选队列 / 会话级临时 / 长期」三类，靠
-- status+scope+expires_at 表达生命周期，冲突收敛只能靠代码（写侧 + 批处理两处都要维护）。
-- 这里用 UNIQUE(user_id, memory_key) 把「同一意图槽位只能有一条生效」变成**结构约束**：
-- 图表偏好一个槽、粒度一个槽、每条事实一个内容指纹槽；换说法/换取值 = UPSERT 覆盖，
-- 旧内容留在 previous_content（一步回溯），revision 计数。
CREATE TABLE IF NOT EXISTS memory.long_term_memory (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    memory_key VARCHAR(96) NOT NULL,
    content TEXT NOT NULL,
    memory_type VARCHAR(32) NOT NULL,
    importance_score REAL DEFAULT 0.8,
    intent_embedding VECTOR(1536),
    source VARCHAR(64),
    confidence VARCHAR(16) DEFAULT 'high',
    evidence_count INT DEFAULT 1,
    revision INT DEFAULT 1,
    previous_content TEXT,
    origin_id INT,
    access_count INT DEFAULT 0,
    last_access_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, memory_key)
);

CREATE INDEX IF NOT EXISTS idx_long_term_user
    ON memory.long_term_memory (user_id);
-- 灰区复审队列（plan 2026-11-10-semantic-memory-merge D5）：
-- 结构信号缺失、但 embedding 余弦 >= 0.90 的候选对——**只登记，不合并**。
-- 攒够同一类的条目，就该往 metrics.yaml 的 synonyms 或本模块词表里加一条同义说法；
-- 这是「词表缺口」的可观测输入，不是垃圾桶。
CREATE TABLE IF NOT EXISTS memory.merge_candidate (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL,
    candidate_content TEXT NOT NULL,
    target_id INT,
    target_content TEXT,
    similarity REAL,
    reason VARCHAR(48),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, candidate_content, target_id)
);

CREATE INDEX IF NOT EXISTS idx_merge_candidate_user
    ON memory.merge_candidate (user_id, created_at DESC);
-- ─────────────────────────────────────────────────────────────────────────────
-- 重试尝试（plan 2026-11-11-failure-recovery-durability P1）
-- 为什么落库：retry_counters 原先只活在图的 State（dev=MemorySaver），进程重启
-- 即归零 → 预算可被无限绕过。这张表同时是审计（每次重试一行）与播种源。
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent.retry_attempt (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    user_id INT NOT NULL,
    requirement_draft_id BIGINT,
    attempt INT NOT NULL,
    -- sql_generation / plan / mcp_schema（DiagnosePolicy 的预算维度）/ auto（P2 自动重试）
    budget_key VARCHAR(24) NOT NULL,
    -- retry_sql / replan / retry_mcp_schema_retrieval / auto_retry
    action VARCHAR(48) NOT NULL,
    error_kind VARCHAR(32),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retry_attempt_session
    ON agent.retry_attempt (session_id, requirement_draft_id, created_at DESC);
