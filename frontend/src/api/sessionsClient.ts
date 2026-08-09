/**
 * REST client for `/api/v1/sessions/*` and `/api/v1/sessions/{sid}/reports/{v}`.
 */
import { handleUnauthorized } from './unauthorized'

function getAuthToken(): string | null {
  try {
    const raw = localStorage.getItem('ragent_auth')
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed?.state?.token ?? null
  } catch {
    return null
  }
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, headers: { ...authHeaders(), ...(init?.headers || {}) } })
  if (res.status === 401) {
    handleUnauthorized()
    throw new Error(`${url} failed: 401`)
  }
  if (!res.ok) {
    throw new Error(`${url} failed: ${res.status}`)
  }
  return (await res.json()) as T
}

export interface SessionSummary {
  session_id: string
  title: string
  phase: string
  msg_count: number
  updated_at: string
  report_versions: Array<{
    version: number
    title: string
    status: string
    created_at: string
    favorite: boolean
  }>
  /** Convenience: first user message in the conversation. Backend
   * populates this from `app.conversations` content; older sessions
   * may return `undefined`. */
  first_message?: string
  last_message?: string
}

export interface SessionSnapshot {
  session: SessionSummary
  messages: any[]
  current_requirement: any | null
  latest_report: any | null
  last_failed_action: string | null
}

export interface ReportVersionDetail {
  id: number
  session_id: string
  user_id: number
  version: number
  parent_version: number | null
  requirement_draft_id: number | null
  adjustment_text: string | null
  title: string
  status: string
  report_payload: any
  query_snapshot: any | null
  trace_id: string | null
  favorite: boolean
  created_at: string
  /** SUCCESS / EMPTY / FAILED from the backend verdict. Older rows
   *  (written before the three-state split) return undefined — the
   *  front-end treats those as SUCCESS for back-compat. */
  execution_status?: 'SUCCESS' | 'EMPTY' | 'FAILED' | null
}

export function fetchSessions(
  limit: number = 30,
  offset: number = 0,
): Promise<{ sessions: SessionSummary[] }> {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) }).toString()
  return jsonFetch(`/api/v1/sessions?${qs}`)
}

export function fetchSession(sessionId: string): Promise<SessionSnapshot | null> {
  return jsonFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`)
}

export function fetchReportVersion(
  sessionId: string,
  version: number,
): Promise<{ report: ReportVersionDetail }> {
  return jsonFetch(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/reports/${version}`,
  )
}

export async function patchRequirement(
  sessionId: string,
  requirement: any,
): Promise<{ requirement: any }> {
  return jsonFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/requirement`, {
    method: 'PATCH',
    body: JSON.stringify({ requirement }),
  })
}

// Plan B (2026-08-09) — 跟 backend 的 list_sessions default 30 对齐
export const SESSIONS_PAGE_SIZE = 30
