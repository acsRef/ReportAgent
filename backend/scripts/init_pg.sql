-- ReportAgent PostgreSQL Schema Initialization
-- Run: psql -U ragent -d ragent -f init_pg.sql
-- This file is idempotent; safe to re-run.

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
    created_at TIMESTAMP DEFAULT NOW()
);

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

