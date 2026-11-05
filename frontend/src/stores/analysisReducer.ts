import type {
  AnalysisError,
  AnalysisPhase,
  ConversationMessage,
  ReportVersion,
  SessionSummary,
} from '../types/analysis'
import type { RequirementCard } from '../types/requirement'
import type { TimelineEntry } from '../types/report'
import {
  appendTurn,
  closeTrace,
  mergeServerHistory,
  withAssistantStatus,
  withAssistantText,
  withAssistantVersion,
  withTraceEntry,
  withTraceNote,
  type ChatMessage,
  type ChatStatus,
  type SentTurn,
  type TimelineCloseReason,
} from './chatTranscript'

export type { ChatMessage, ChatStatus, SentTurn, TimelineCloseReason }

export const SESSIONS_PAGE_SIZE = 30

export interface AnalysisState {
  phase: AnalysisPhase
  activeSessionId: string | null
  sessions: SessionSummary[]
  requirement: RequirementCard | null
  reportVersions: ReportVersion[]
  selectedReportVersion: number | null
  /**
   * 完整对话账本（append-only）——用户消息 + 助手消息（含过程与版本）。
   *
   * 2026-11-05（plan conversation-transcript-messages 修订 A）：语义从
   * 「服务端历史快照」变成「本地对话账本」：发送即追加，SSE 增量更新最后一条，
   * 服务端历史按 `serverId` 合并（见 `chatTranscript.mergeServerHistory`）。
   * 历史轮次与当轮都从它派生，不再有第二份「当轮」状态。
   */
  messages: ChatMessage[]
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
  /** 发送：一次原子追加「用户消息 + 空的助手占位（streaming）」。 */
  | { type: 'chat/turn-appended'; turn: SentTurn }
  /** 助手这句话的正文（闲聊回复 / 服务端回填的 assistant_text）。 */
  | { type: 'chat/text'; text: string }
  /** 生命周期：streaming → done / error。 */
  | { type: 'chat/status'; status: ChatStatus }
  /** 该轮产出的报告版本号（挂到最后一条助手消息上）。 */
  | { type: 'chat/version'; version: number }
  /** 合并服务端历史（按 id 去重；不整表覆盖）。 */
  | { type: 'chat/merged'; messages: ConversationMessage[] }
  | { type: 'report/received'; report: ReportVersion }
  | { type: 'report/selected'; version: number }
  | { type: 'timeline/received'; entry: TimelineEntry }
  | { type: 'timeline/note'; note: string | null }
  /** 一轮结束时的收尾：把还挂在「进行中」的步骤按原因定性。 */
  | { type: 'timeline/closed'; reason: TimelineCloseReason }
  | { type: 'analysis/failed'; error: AnalysisError }
  | { type: 'analysis/reset' }

export const initialAnalysisState: AnalysisState = {
  phase: 'idle',
  activeSessionId: null,
  sessions: [],
  requirement: null,
  reportVersions: [],
  selectedReportVersion: null,
  messages: [],
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

    case 'chat/turn-appended':
      // 发送即 append：用户那句 + 空的助手占位，一次动作完成（不允许只加一半）。
      return { ...state, messages: appendTurn(state.messages, action.turn) }

    case 'chat/text':
      return { ...state, messages: withAssistantText(state.messages, action.text) }

    case 'chat/status':
      return { ...state, messages: withAssistantStatus(state.messages, action.status) }

    case 'chat/version':
      return { ...state, messages: withAssistantVersion(state.messages, action.version) }

    case 'chat/merged':
      // 按 serverId 去重合并；本地还在流的占位一条都不会被删。
      return { ...state, messages: mergeServerHistory(state.messages, action.messages) }

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

    case 'timeline/received':
      // 过程步骤写进**最后一条助手消息**（「过程融进同一条回复」）。
      return { ...state, messages: withTraceEntry(state.messages, action.entry) }

    case 'timeline/note':
      return { ...state, messages: withTraceNote(state.messages, action.note) }

    case 'timeline/closed':
      return { ...state, messages: closeTrace(state.messages, action.reason) }

    case 'analysis/failed':
      return { ...state, phase: 'error', error: action.error }

    case 'analysis/reset':
      return {
        ...initialAnalysisState,
        sessions: state.sessions,
      }
  }
}
