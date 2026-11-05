-- 清理测试 / 探针会话（一次性维护脚本，不进启动流程）。
--
-- 用法（`before` 之后创建的会话会被删，时间是 **UTC**，因为 created_at 是
-- `timestamp without time zone` 且写入用 `NOW()`）：
--
--   docker exec -i ragent-postgres psql -U ragent -d ragent \
--     -v before="'2026-11-05 00:00'" -f - < backend/scripts/cleanup_test_sessions.sql
--
-- 也会清掉：草稿 / 报告版本 / 会话消息（含 turn_trace）/ 任务审计 / 会话级语义记忆 /
-- 该会话的 trace 与 span / LangGraph checkpoint（非 dev 环境才有）。
-- 一句话：删完这些会话就像没跑过。
\set ON_ERROR_STOP on

BEGIN;

CREATE TEMP TABLE doomed_sessions ON COMMIT DROP AS
SELECT thread_id AS session_id, user_id
  FROM agent.session
 WHERE created_at >= :'before'::timestamp;

CREATE TEMP TABLE doomed_traces ON COMMIT DROP AS
SELECT DISTINCT trace_id
  FROM observability.agent_trace
 WHERE session_id IN (SELECT session_id FROM doomed_sessions);

SELECT count(*) AS sessions_to_delete FROM doomed_sessions;

-- 会话账本（user / assistant_text / report_version 指针 / turn_trace）
DELETE FROM app.conversations WHERE session_id IN (SELECT session_id FROM doomed_sessions);

-- 任务变更审计
DELETE FROM agent.analysis_task_revision WHERE session_id IN (SELECT session_id FROM doomed_sessions);

-- 报告版本（先于 requirement_draft：report_version.requirement_draft_id 是外键）
DELETE FROM agent.report_version WHERE session_id IN (SELECT session_id FROM doomed_sessions);

-- 会话级语义记忆（temporary_preference 等绑 session 的条目）
DELETE FROM memory.semantic_entry WHERE session_id IN (SELECT session_id FROM doomed_sessions);

-- 可观测：llm_call → span → trace
DELETE FROM observability.llm_call
 WHERE span_id IN (
   SELECT span_id FROM observability.agent_trace_span
    WHERE trace_id IN (SELECT trace_id FROM doomed_traces)
 );
DELETE FROM observability.agent_trace_span WHERE trace_id IN (SELECT trace_id FROM doomed_traces);
DELETE FROM observability.agent_trace WHERE session_id IN (SELECT session_id FROM doomed_sessions);

-- LangGraph checkpoint（dev 用 MemorySaver，其余环境落库）
DELETE FROM public.checkpoint_writes WHERE thread_id IN (SELECT session_id FROM doomed_sessions);
DELETE FROM public.checkpoint_blobs WHERE thread_id IN (SELECT session_id FROM doomed_sessions);
DELETE FROM public.checkpoints WHERE thread_id IN (SELECT session_id FROM doomed_sessions);

-- 草稿：session.latest_requirement_draft_id 先断开，否则外键挡住
UPDATE agent.session SET latest_requirement_draft_id = NULL
 WHERE thread_id IN (SELECT session_id FROM doomed_sessions);
DELETE FROM agent.requirement_draft WHERE session_id IN (SELECT session_id FROM doomed_sessions);

DELETE FROM agent.session WHERE thread_id IN (SELECT session_id FROM doomed_sessions);

COMMIT;

-- 残留检查（应为 0）
SELECT
  (SELECT count(*) FROM agent.session) AS sessions_left,
  (SELECT count(*) FROM app.conversations) AS conversations_left,
  (SELECT count(*) FROM agent.report_version) AS versions_left,
  (SELECT count(*) FROM agent.requirement_draft) AS drafts_left;
