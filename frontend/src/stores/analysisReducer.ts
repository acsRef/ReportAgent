import type {
  AnalysisError,
  AnalysisPhase,
  ConversationMessage,
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
  /** 完整对话消息（快照恢复 + 每轮结束后刷新）——历史轮次时间线的数据源。 */
  messages: ConversationMessage[]
  /** 「执行过程」步骤（trace 帧，按节点去重）——2026-11-04 起真正接线。 */
  timeline: TimelineEntry[]
  /**
   * 过程面板的异常说明（中断/停止/超时/重连/后台完成）。
   * 与「步骤」分开存：说明是临时提醒，不该混进步骤列表。
   */
  timelineNote: string | null
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
  | { type: 'messages/received'; messages: ConversationMessage[] }
  | { type: 'report/received'; report: ReportVersion }
  | { type: 'report/selected'; version: number }
  | { type: 'timeline/received'; entry: TimelineEntry }
  | { type: 'timeline/cleared' }
  | { type: 'timeline/note'; note: string | null }
  /** 一轮结束时的收尾：把还挂在「进行中」的步骤按原因定性。 */
  | { type: 'timeline/closed'; reason: TimelineCloseReason }
  | { type: 'analysis/failed'; error: AnalysisError }
  | { type: 'analysis/reset' }

/** 过程收尾原因：done=正常收尾 / stopped=用户停止 / interrupted=断流 / error=后端报错。 */
export type TimelineCloseReason = 'done' | 'stopped' | 'interrupted' | 'error'

const CLOSE_NOTE: Record<TimelineCloseReason, string | null> = {
  done: null,
  stopped: '已停止显示，任务在后台继续执行，完成后会自动更新',
  interrupted: '连接中断，任务可能在后台继续；恢复后会自动更新',
  error: '执行在中途失败，已完成的步骤保留在上方',
}

export const initialAnalysisState: AnalysisState = {
  phase: 'idle',
  activeSessionId: null,
  sessions: [],
  requirement: null,
  reportVersions: [],
  selectedReportVersion: null,
  messages: [],
  timeline: [],
  timelineNote: null,
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
 * 稳定相位（后台任务已无在跑的可能性）。
 *
 * 2026-11-03：后台轮询的完成判定从「phase ∈ {report_ready,error}」改成
 * 「!isBusyPhase(phase)」——解析轮（parsing）同样会后台跑完，只认报告相位会让
 * 「停止 / 断连后跟随解析结果」失效（hand-off P0）。单一来源放在这里，
 * 页面与测试都引用它。
 */
export function isSettledPhase(phase: AnalysisPhase | string): boolean {
  return !BUSY_PHASES.has(phase as AnalysisPhase)
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

    case 'messages/received':
      return { ...state, messages: action.messages }

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
      // 按 **nodeName** 去重（2026-11-04）：progress 帧是「同一节点 running → success」，
      // 每帧 id 都不同，按 id 去重会让步骤列表越叠越长。
      const existingIndex = state.timeline.findIndex(
        (entry) => entry.nodeName === action.entry.nodeName,
      )
      const timeline = [...state.timeline]
      if (existingIndex >= 0) timeline[existingIndex] = action.entry
      else timeline.push(action.entry)
      return { ...state, timeline, timelineNote: null }
    }

    case 'timeline/cleared':
      return { ...state, timeline: [], timelineNote: null }

    case 'timeline/note':
      return { ...state, timelineNote: action.note }

    case 'timeline/closed': {
      // 还挂在「进行中」的步骤按原因定性：
      //   done → success（这一轮正常跑完，最后一步只是没来得及发 success 帧）
      //   stopped → pending（用户停止观看，后台还在跑）
      //   interrupted / error → error（真的断在这里）
      const status: TimelineEntry['status'] =
        action.reason === 'done'
          ? 'success'
          : action.reason === 'stopped'
            ? 'pending'
            : 'error'
      const timeline = state.timeline.map((entry) =>
        entry.status === 'running' ? { ...entry, status } : entry,
      )
      const note =
        action.reason === 'done' && timeline.every((e) => e.status !== 'running')
          ? null
          : CLOSE_NOTE[action.reason]
      return { ...state, timeline, timelineNote: note }
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
