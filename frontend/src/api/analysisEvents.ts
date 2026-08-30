import type { AnalysisError, AnalysisPhase } from '../types/analysis'
import type { RequirementCard } from '../types/requirement'
import type { SSEEvent, TimelineEntry, TraceProgressKind } from '../types/report'

const ANALYSIS_PHASES: ReadonlySet<string> = new Set<AnalysisPhase>([
  'idle',
  'parsing',
  'awaiting_missing',
  'awaiting_confirm',
  'generating',
  'adjusting',
  'report_ready',
  'error',
])

const FAILED_ACTIONS: ReadonlySet<string> = new Set([
  'new',
  'supplement',
  'confirm',
  'adjust',
  'retry',
  // `sql` was added when the backend started emitting structured
  // error envelopes from the SQL sub-graph (timeouts / missing
  // tables / connection issues). Without it the parser would drop
  // the frame because the value isn't in the known set.
  'sql',
])

const TRACE_KINDS: ReadonlySet<string> = new Set<TraceProgressKind>([
  'agent',
  'tool',
  'sql',
  'repair',
  'report',
])

/** P11：report 事件 wire 形态（docs/sse-v2.md）—— `version` 可缺表示
 * 闲聊回复（`{answer:{text}}`），完整报告必有 version/title/answer。 */
export interface ReportEventPayload {
  version?: number
  parent_version?: number | null
  title?: string
  answer: { text?: string; table?: unknown; chart?: unknown; insight?: string | null }
  trace?: unknown[]
}

export type AnalysisStreamEvent =
  | { type: 'phase'; phase: AnalysisPhase; reason?: string }
  | { type: 'requirement'; requirement: RequirementCard }
  | { type: 'trace'; entry: TimelineEntry }
  | { type: 'thinking'; phase?: string; text?: string }
  | { type: 'report'; report: ReportEventPayload }
  | { type: 'error'; error: AnalysisError }
  | { type: 'done'; finalPhase: AnalysisPhase }

export function parseAnalysisSSEEvent(event: SSEEvent): AnalysisStreamEvent | null {
  const payload = parseObject(event.data)
  if (!payload) return null

  switch (event.event as string) {
    case 'phase': {
      const phase = readPhase(payload.phase)
      if (!phase) return null
      return {
        type: 'phase',
        phase,
        ...(typeof payload.reason === 'string' ? { reason: payload.reason } : {}),
      }
    }

    case 'requirement':
      return isRequirementCard(payload)
        ? { type: 'requirement', requirement: payload as RequirementCard }
        : null

    case 'trace': {
      const entry = readTimelineEntry(payload)
      return entry ? { type: 'trace', entry } : null
    }

    case 'thinking':
      return { type: 'thinking', ...(readOptionalString(payload, 'phase', {})), ...(readOptionalString(payload, 'text', {})) }

    case 'report': {
      const report = readReportPayload(payload)
      return report ? { type: 'report', report } : null
    }

    case 'error': {
      const error = readAnalysisError(payload)
      return error ? { type: 'error', error } : null
    }

    case 'done': {
      const finalPhase = readPhase(payload.final_phase)
      return finalPhase ? { type: 'done', finalPhase } : null
    }

    default:
      return null
  }
}

export function isAnalysisPhase(value: unknown): value is AnalysisPhase {
  return typeof value === 'string' && ANALYSIS_PHASES.has(value)
}

function parseObject(data: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(data)
    return typeof value === 'object' && value !== null
      ? value as Record<string, unknown>
      : null
  } catch {
    return null
  }
}

function readPhase(value: unknown): AnalysisPhase | null {
  return typeof value === 'string' && ANALYSIS_PHASES.has(value)
    ? value as AnalysisPhase
    : null
}

function readOptionalString(payload: Record<string, unknown>, key: string, fallback: Record<string, string>): Partial<Record<string, string>> {
  return typeof payload[key] === 'string' ? { [key]: payload[key] as string } : fallback
}

function readTimelineEntry(payload: Record<string, unknown>): TimelineEntry | null {
  if (typeof payload.step !== 'string') return null
  const status = payload.status
  if (status !== 'running' && status !== 'success' && status !== 'error' && status !== 'pending') {
    return null
  }
  const rawKind = payload.kind
  const kind: TraceProgressKind | undefined =
    typeof rawKind === 'string' && TRACE_KINDS.has(rawKind)
      ? rawKind as TraceProgressKind
      : undefined
  return {
    id: `${Date.now()}-${payload.step}`,
    nodeName: payload.step,
    status,
    ...(typeof payload.detail === 'string' ? { detail: payload.detail } : {}),
    ...(kind ? { kind } : {}),
    timestamp: Date.now(),
  }
}

function readReportPayload(payload: Record<string, unknown>): ReportEventPayload | null {
  if (typeof payload.answer !== 'object' || payload.answer === null) return null
  const answer = payload.answer as ReportEventPayload['answer']
  const version = payload.version
  if (version !== undefined && typeof version !== 'number') return null
  const parentVersion = payload.parent_version
  if (parentVersion !== undefined && parentVersion !== null && typeof parentVersion !== 'number') return null
  return {
    version: version as number | undefined,
    parent_version: parentVersion as number | null | undefined,
    ...(typeof payload.title === 'string' ? { title: payload.title } : {}),
    answer,
    ...(Array.isArray(payload.trace) ? { trace: payload.trace } : {}),
  }
}

const ERROR_KINDS: ReadonlySet<string> = new Set([
  'timeout',
  'syntax',
  'object',
  'connection',
  'permission',
  'other',
])

function readAnalysisError(payload: Record<string, unknown>): AnalysisError | null {
  if (
    typeof payload.code !== 'string' ||
    typeof payload.message !== 'string' ||
    typeof payload.recoverable !== 'boolean'
  ) {
    return null
  }

  const failedAction = payload.failed_action
  if (
    failedAction !== null &&
    (typeof failedAction !== 'string' || !FAILED_ACTIONS.has(failedAction))
  ) {
    return null
  }

  // Optional fields: kind + sql. Backend always emits them for the
  // structured error helper; we keep them optional so older servers
  // (or HTTP_ERROR / NETWORK_ERROR / INTERNAL events) still parse.
  const rawKind = payload.kind
  const kind: AnalysisError['kind'] =
    typeof rawKind === 'string' && ERROR_KINDS.has(rawKind)
      ? (rawKind as AnalysisError['kind'])
      : null
  const rawSql = payload.sql
  const sql: string | null = typeof rawSql === 'string' ? rawSql : null

  return {
    code: payload.code,
    message: payload.message,
    recoverable: payload.recoverable,
    failed_action: failedAction as AnalysisError['failed_action'],
    kind,
    sql,
  }
}

// Exported for the unit test in __tests__/analysisEvents.test.ts so we
// can drive readAnalysisError without going through the SSE decoder.
export const __test__ = { readAnalysisError }

function isRequirementCard(payload: unknown): payload is RequirementCard {
  if (typeof payload !== 'object' || payload === null) return false
  const value = payload as Record<string, unknown>
  return (
    typeof value.id === 'string' &&
    typeof value.version === 'number' &&
    typeof value.status === 'string' &&
    typeof value.summary === 'string' &&
    Array.isArray(value.missing_fields)
  )
}