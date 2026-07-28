import type {
  AnalysisError,
  AnalysisPhase,
  ReportVersion,
  SessionSummary,
} from '../types/analysis'
import type { RequirementCard } from '../types/requirement'
import type { TimelineEntry } from '../types/report'

export interface AnalysisState {
  phase: AnalysisPhase
  activeSessionId: string | null
  sessions: SessionSummary[]
  requirement: RequirementCard | null
  reportVersions: ReportVersion[]
  selectedReportVersion: number | null
  timeline: TimelineEntry[]
  error: AnalysisError | null
}

export type AnalysisAction =
  | { type: 'phase/received'; phase: AnalysisPhase }
  | { type: 'session/selected'; sessionId: string }
  | { type: 'sessions/received'; sessions: SessionSummary[] }
  | { type: 'requirement/received'; requirement: RequirementCard }
  | { type: 'report/received'; report: ReportVersion }
  | { type: 'report/selected'; version: number }
  | { type: 'timeline/received'; entry: TimelineEntry }
  | { type: 'analysis/failed'; error: AnalysisError }
  | { type: 'analysis/reset' }

export const initialAnalysisState: AnalysisState = {
  phase: 'idle',
  activeSessionId: null,
  sessions: [],
  requirement: null,
  reportVersions: [],
  selectedReportVersion: null,
  timeline: [],
  error: null,
}

const BUSY_PHASES: ReadonlySet<AnalysisPhase> = new Set([
  'parsing',
  'generating',
  'adjusting',
])

export function isBusyPhase(phase: AnalysisPhase): boolean {
  return BUSY_PHASES.has(phase)
}

/**
 * True when the current error is recoverable AND lives in a phase the
 * retry endpoint knows how to replay.
 *
 * Originally only `failed_action === 'confirm'` qualified, but the
 * backend now emits structured SQL errors (`failed_action === 'sql'`)
 * with `recoverable: true` for transient failures (timeout / connection /
 * object-not-found). These should also be retryable because the user
 * might want to retry with the same requirement + a smaller time range,
 * which the retry endpoint supports.
 */
export function canRetryFailedAction(
  state: Pick<AnalysisState, 'phase' | 'error'>,
): boolean {
  return (
    state.phase === 'error' &&
    state.error !== null &&
    state.error.recoverable === true &&
    (state.error.failed_action === 'confirm' || state.error.failed_action === 'sql')
  )
}

export function analysisReducer(
  state: AnalysisState,
  action: AnalysisAction,
): AnalysisState {
  switch (action.type) {
    case 'phase/received':
      return {
        ...state,
        phase: action.phase,
        error: action.phase === 'error' ? state.error : null,
      }

    case 'session/selected':
      return {
        ...initialAnalysisState,
        activeSessionId: action.sessionId,
        sessions: state.sessions,
      }

    case 'sessions/received':
      return { ...state, sessions: action.sessions }

    case 'requirement/received':
      if (
        state.requirement?.id === action.requirement.id &&
        state.requirement.version > action.requirement.version
      ) {
        return state
      }
      return { ...state, requirement: action.requirement }

    case 'report/received': {
      const existingIndex = state.reportVersions.findIndex(
        (report) => report.version === action.report.version,
      )
      const reportVersions = [...state.reportVersions]
      if (existingIndex >= 0) {
        reportVersions[existingIndex] = action.report
      } else {
        reportVersions.push(action.report)
        reportVersions.sort((left, right) => left.version - right.version)
      }
      return {
        ...state,
        phase: 'report_ready',
        reportVersions,
        selectedReportVersion: action.report.version,
        error: null,
      }
    }

    case 'report/selected':
      if (!state.reportVersions.some((report) => report.version === action.version)) {
        return state
      }
      return { ...state, selectedReportVersion: action.version }

    case 'timeline/received': {
      const existingIndex = state.timeline.findIndex(
        (entry) => entry.id === action.entry.id,
      )
      const timeline = [...state.timeline]
      if (existingIndex >= 0) timeline[existingIndex] = action.entry
      else timeline.push(action.entry)
      return { ...state, timeline }
    }

    case 'analysis/failed':
      return { ...state, phase: 'error', error: action.error }

    case 'analysis/reset':
      return {
        ...initialAnalysisState,
        sessions: state.sessions,
      }
  }
}
