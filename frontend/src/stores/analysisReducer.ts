import type {
  AnalysisError,
  AnalysisPhase,
  ReportVersion,
  SessionSummary,
} from '../types/analysis'
import type { RequirementCard } from '../types/requirement'
import type { TimelineEntry } from '../types/report'

export const SESSIONS_PAGE_SIZE = 30

export interface AnalysisState {
  phase: AnalysisPhase
  activeSessionId: string | null
  sessions: SessionSummary[]
  requirement: RequirementCard | null
  reportVersions: ReportVersion[]
  selectedReportVersion: number | null
  timeline: TimelineEntry[]
  error: AnalysisError | null
  // Pagination (Plan B 步骤 2, 2026-08-09) — session 列表分页状态：
  // sessionsOffset = 已加载条数；hasMoreSessions = 服务端是否还有下一页；
  // sessionsPageLoading = loadMore 触发中（防双击 / 防覆盖旧 sessions）。
  sessionsOffset: number
  hasMoreSessions: boolean
  sessionsPageLoading: boolean
}

export type AnalysisAction =
  | { type: 'phase/received'; phase: AnalysisPhase }
  | { type: 'session/selected'; sessionId: string }
  | { type: 'sessions/page-received'; sessions: SessionSummary[]; hasMore: boolean }
  | { type: 'sessions/page-appended'; sessions: SessionSummary[]; hasMore: boolean }
  | { type: 'sessions/page-loading'; loading: boolean }
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
  sessionsOffset: 0,
  hasMoreSessions: true,
  sessionsPageLoading: false,
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
  // P-1: REQUIREMENT_INCOMPLETE 的错误事件以 failed_action = 当前 mode
  // （'new' | 'supplement' | 'adjust'）出现且 recoverable: true，此前白名单
  // 只认 'confirm' | 'sql'，导致这类可恢复错误不显示重试按钮。补齐。
  const action = state.error?.failed_action
  return (
    state.phase === 'error' &&
    state.error !== null &&
    state.error.recoverable === true &&
    (action === 'confirm' ||
      action === 'sql' ||
      action === 'new' ||
      action === 'supplement' ||
      action === 'adjust')
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

    case 'sessions/received': {
      // 兼容旧 dispatch（一次性把整批替换，但**不再**设置 sessionsOffset），
      // 由调用方在新逻辑中改用 'sessions/page-received'。
      return { ...state, sessions: action.sessions }
    }

    case 'sessions/page-received':
      // 首次拉取（reset + 第一页），覆盖 sessions 与 offset
      return {
        ...state,
        sessions: action.sessions,
        sessionsOffset: action.sessions.length,
        hasMoreSessions: action.hasMore,
        sessionsPageLoading: false,
      }

    case 'sessions/page-appended':
      // loadMore（增量追加），offset 累加
      return {
        ...state,
        sessions: [...state.sessions, ...action.sessions],
        sessionsOffset: state.sessionsOffset + action.sessions.length,
        hasMoreSessions: action.hasMore,
        sessionsPageLoading: false,
      }

    case 'sessions/page-loading':
      return { ...state, sessionsPageLoading: action.loading }

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
