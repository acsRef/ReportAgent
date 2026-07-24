import type { AnalysisError, AnalysisPhase, ReportVersion } from '../types/analysis'
import type { RequirementCard } from '../types/requirement'
import type { SSEEvent, TimelineEntry } from '../types/report'

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
])

export type AnalysisStreamEvent =
  | { type: 'phase'; phase: AnalysisPhase; reason?: string }
  | { type: 'requirement'; requirement: RequirementCard }
  | { type: 'trace'; entry: TimelineEntry }
  | { type: 'report'; report: ReportVersion }
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

    case 'report':
      return isReportVersion(payload)
        ? { type: 'report', report: payload as ReportVersion }
        : null

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

  return {
    code: payload.code,
    message: payload.message,
    recoverable: payload.recoverable,
    failed_action: failedAction as AnalysisError['failed_action'],
  }
}

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

function isReportVersion(payload: unknown): payload is ReportVersion {
  if (typeof payload !== 'object' || payload === null) return false
  const value = payload as Record<string, unknown>
  return (
    typeof value.id === 'string' &&
    typeof value.session_id === 'string' &&
    typeof value.version === 'number' &&
    typeof value.title === 'string' &&
    typeof value.report === 'object' &&
    value.report !== null
  )
}
