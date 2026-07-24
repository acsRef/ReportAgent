# PostgreSQL Persistence

> 完整 DDL 文档，与 [`2026-07-24-conversational-workbench.md`](../plans/2026-07-24-conversational-workbench.md) 第 4 节配套。本文件描述的 SQL 应合并到 `backend/scripts/init_pg.sql`。

## 1. agent.requirement_draft

```sql
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
```

## 2. agent.report_version

```sql
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
```

## 3. app.report_template

```sql
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
```

## 4. agent.session 扩展

使用现有 `agent.session` 表，扩展字段：

```sql
ALTER TABLE agent.session
    ADD COLUMN IF NOT EXISTS latest_requirement_draft_id BIGINT,
    ADD COLUMN IF NOT EXISTS latest_report_version INT,
    ADD COLUMN IF NOT EXISTS current_phase VARCHAR(32) NOT NULL DEFAULT 'idle',
    ADD COLUMN IF NOT EXISTS last_failed_action VARCHAR(32);

ALTER TABLE agent.session
    ADD CONSTRAINT fk_session_latest_requirement
    FOREIGN KEY (latest_requirement_draft_id)
    REFERENCES agent.requirement_draft(id);
```

- `current_phase` 存储最近一次 `phase` 事件值；`idle` 初始。
- `last_failed_action` 仅在 `error` 阶段有值；retry 后清空。
- `latest_requirement_draft_id` / `latest_report_version` 由 service 层在写新版本时同步。

## 5. app.conversations 约定

`app.conversations.metadata` 仅作为索引，不再承载完整 RequirementCard：

```json
{
  "requirement_version": 3,
  "report_version": 2,
  "card_snapshot": { ... }
}
```

`card_snapshot` 仅用于历史消息回看展示；任何修改必须基于最新 RequirementCard 与 report_version 行。

## 6. 并发与事务

- 版本号在事务内 `MAX(version) + 1`，配 unique constraint。
- requirement draft 创建/更新 与 conversation pointer 写同一事务。
- report version 创建 与 session latest 更新 与 conversation pointer 写同一事务。
- 错误路径必须回滚所有写入。
