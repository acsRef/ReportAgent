-- ReportAgent PostgreSQL Schema Initialization
-- Run: psql -U ragent -d ragent -f init_pg.sql

CREATE SCHEMA IF NOT EXISTS agent;
CREATE SCHEMA IF NOT EXISTS memory;
CREATE SCHEMA IF NOT EXISTS observability;

-- ============================================================
-- agent schema
-- ============================================================

CREATE TABLE IF NOT EXISTS agent.session (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64),
    title VARCHAR(256),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_checkpoint_at TIMESTAMP,  -- nullable: LangGraph checkpoint may not exist yet
    status VARCHAR(32) DEFAULT 'active'
);

-- ============================================================
-- memory schema
-- ============================================================

CREATE TABLE IF NOT EXISTS memory.query_template (
    id SERIAL PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS memory.semantic_entry (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_semantic_entry_user ON memory.semantic_entry (user_id);

-- ============================================================
-- observability schema
-- ============================================================

CREATE TABLE IF NOT EXISTS observability.agent_trace (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) UNIQUE NOT NULL,
    session_id VARCHAR(64),
    user_query TEXT,
    status VARCHAR(32),
    start_time TIMESTAMP DEFAULT NOW(),
    end_time TIMESTAMP,
    total_duration_ms INT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_trace_session ON observability.agent_trace (session_id);
CREATE INDEX IF NOT EXISTS idx_agent_trace_trace_id ON observability.agent_trace (trace_id);

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
-- app schema (auth + conversation)
-- ============================================================

CREATE SCHEMA IF NOT EXISTS app;

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
