/**
 * REST client for `/api/v1/observability/*` — trace 链路 + 聚合指标（只读）。
 * 见 docs/plans/2026-08-01-observability-ops.md。
 */
import { handleUnauthorized } from './unauthorized'

function getAuthToken(): string | null {
  try {
    const raw = localStorage.getItem('ragent_auth')
    if (!raw) return null
    return JSON.parse(raw)?.state?.token ?? null
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

export interface TraceSummary {
  trace_id: string
  session_id: string | null
  user_query: string | null
  status: string
  start_time: string | null
  end_time: string | null
  total_duration_ms: number | null
}

export interface SpanDetail {
  span_id: string
  parent_span_id: string | null
  span_name: string
  span_type: string
  start_time: string | null
  duration_ms: number | null
  status: string
  error: string | null
}

export interface LlmCallDetail {
  model: string
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
  cost: number | null
}

export interface Metrics {
  trace_total: number
  status_breakdown: Record<string, number>
  success_rate: number | null
  avg_duration_ms: number | null
  p95_duration_ms: number | null
  llm_call_total: number
  llm_tokens_total: number
  llm_avg_latency_ms: number | null
}

export interface TraceDetail {
  trace: TraceSummary
  spans: SpanDetail[]
  llm_calls: LlmCallDetail[]
}

export function fetchMetrics(): Promise<Metrics> {
  return jsonFetch('/api/v1/observability/metrics')
}

export function fetchTraces(
  limit = 50,
  offset = 0,
  status?: string,
): Promise<{ traces: TraceSummary[]; limit: number; offset: number }> {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (status) qs.set('status', status)
  return jsonFetch(`/api/v1/observability/traces?${qs.toString()}`)
}

export function fetchTraceDetail(traceId: string): Promise<TraceDetail> {
  return jsonFetch(`/api/v1/observability/traces/${encodeURIComponent(traceId)}`)
}
