import type { ReportResponse } from './report'

export type AnalysisPhase =
  | 'idle'
  | 'parsing'
  | 'awaiting_missing'
  | 'awaiting_confirm'
  | 'generating'
  | 'adjusting'
  | 'report_ready'
  | 'error'

export type ReportVersionStatus = 'generating' | 'done' | 'error'

export interface ReportVersion {
  id: string
  session_id: string
  version: number
  parent_version: number | null
  title: string
  status: ReportVersionStatus
  report: ReportResponse
  created_at: string
}

export interface ReportVersionSummary {
  version: number
  title: string
  status: ReportVersionStatus
  created_at: string
  favorite: boolean
}

/** 会话里的一条消息（后端 /sessions/{sid} 的 messages）。 */
export interface ConversationMessage {
  id: number
  role: 'user' | 'assistant' | 'system'
  /**
   * `text` | `assistant_text` | `report_version` | `requirement_patch` | `turn_trace`。
   * 2026-11-05：`turn_trace` 是「这一轮的思考链路」（content=null，metadata.entries），
   * 只作数据来源，不渲染成气泡。
   */
  message_type: 'text' | 'requirement_patch' | 'report_version' | 'turn_trace' | string
  content: string | null
  /**
   * 指针行 / trace 行的载荷。
   * 注意：后端 asyncpg 没注册 jsonb codec，线上拿到的是 **JSON 字符串**——
   * 读的时候要 `typeof === 'string' ? JSON.parse(...)`。
   */
  metadata?: unknown
  created_at: string
}

export interface SessionSummary {
  session_id: string
  title: string
  phase: AnalysisPhase
  msg_count: number
  updated_at: string
  report_versions: ReportVersionSummary[]
  /** Optional extra fields echoed from the backend's list_sessions
   * (see /api/v1/sessions). Kept optional so older clients don't break. */
  first_message?: string
  last_message?: string
}

export interface AnalysisError {
  code: string
  message: string
  recoverable: boolean
  /** Where the failure happened. `sql` was added when the backend
   *  started emitting structured error envelopes for query execution
   *  failures (timeouts, missing tables, etc.). */
  failed_action: 'new' | 'supplement' | 'confirm' | 'adjust' | 'retry' | 'sql' | null
  /** Failure category from the backend. Drives ErrorCard copy + the
   *  retry-button affordance. Optional for backward compatibility —
   *  legacy events (HTTP_ERROR / NETWORK_ERROR / INTERNAL) have no
   *  kind and fall back to a generic title. */
  kind?:
    | 'timeout'
    | 'syntax'
    | 'object'
    | 'object_not_found'
    | 'object_ambiguous'
    | 'connection'
    | 'permission'
    | 'other'
    | null
  /** The SQL the agent actually tried, clipped to ≤200 chars by the
   *  backend. Used by ErrorCard's collapsible "查看 SQL" disclosure. */
  sql?: string | null
}
