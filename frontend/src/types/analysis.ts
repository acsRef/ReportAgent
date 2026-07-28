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
  kind?: 'timeout' | 'syntax' | 'object' | 'connection' | 'permission' | 'other' | null
  /** The SQL the agent actually tried, clipped to ≤200 chars by the
   *  backend. Used by ErrorCard's collapsible "查看 SQL" disclosure. */
  sql?: string | null
}
