/**
 * 「事件 → store 动作」分发层（P11 事件面整理）。
 *
 * /chat 与 /confirm 流共用同一 `handleSSEEvent`（schema 已在
 * api/analysisEvents 统一）；session 恢复 / 版本刷新作为 store 侧动作
 * 与页面解耦。断言这些都是分析 Reducer 的单一写者，组件只 dispatch。
 */
import { isAnalysisPhase } from '../api/analysisEvents'
import type { AnalysisStreamEvent } from '../api/analysisEvents'
import { fetchSession } from '../api/sessionsClient'
import type { ToastApi } from '../api/confirmStream'
import type { ReportVersionStatus } from '../types/analysis'
import type { TimelineEntry } from '../types/report'
import { useAnalysisStore } from './analysisStore'

export interface StreamEventCtx {
  msgApi: ToastApi
  sessionId: string | null
  setSending: (v: boolean | ((cur: boolean) => boolean)) => void
  setCasualReply: (text: string | null) => void
  /** P11：trace progress 帧实时回调——ProgressCard 真信号源。 */
  onTrace?: (entry: TimelineEntry) => void
}

export function handleSSEEvent(evt: AnalysisStreamEvent, ctx: StreamEventCtx): void {
  const dispatch = useAnalysisStore.getState().dispatch
  switch (evt.type) {
    case 'phase':
      dispatch({ type: 'phase/received', phase: evt.phase })
      break
    case 'requirement':
      dispatch({ type: 'requirement/received', requirement: evt.requirement })
      ctx.setSending(false)
      break
    case 'trace':
      ctx.onTrace?.(evt.entry)
      break
    case 'report': {
      const report = evt.report
      if (typeof report.version === 'number') {
        // P11 F5：adjust/retry 走 /chat 的 report——刷新版本列表并选中最新。
        if (ctx.sessionId) void refreshVersionsAndSelectLatest(ctx.sessionId)
        ctx.msgApi.success(`报告 v${report.version} 已生成`)
      } else if (typeof report.answer?.text === 'string') {
        // P11 F4：chitchat 闲聊回复——casual bubble（不进版本流）。
        ctx.setCasualReply(report.answer.text)
      }
      break
    }
    case 'error':
      ctx.msgApi.error(evt.error.message ?? '处理失败')
      dispatch({ type: 'analysis/failed', error: evt.error })
      ctx.setSending(false)
      break
    case 'done':
      ctx.setSending(false)
      dispatch({ type: 'phase/received', phase: evt.finalPhase })
      break
    default:
      // thinking：提示文案，无需动作。
      break
  }
}

/** 报告版本 summary → 域对象（ReportVersion）；未知状态兜底 done。 */
function toReportVersion(
  v: { version: number; title?: string; status?: string; created_at?: string },
  sessionId: string,
) {
  const status = (['generating', 'done', 'error'] as ReportVersionStatus[]).includes(
    v.status as ReportVersionStatus,
  )
    ? (v.status as ReportVersionStatus)
    : 'done'
  return {
    id: `r-${v.version}`,
    session_id: sessionId,
    version: v.version,
    parent_version: v.version > 1 ? v.version - 1 : null,
    title: v.title ?? '',
    status,
    report: { answer: { text: '' } } as any,
    created_at: v.created_at ?? '',
  }
}

/**
 * 会话快照恢复（session resume）。P11 F6：恢复 requirement + 版本列表后，
 * 以快照 phase 覆写（report/received 的 report_ready 副作用不掩盖真实状态）。
 *
 * @returns true = 会话仍在后台忙（generating/adjusting）——调用方据此恢复轮询。
 */
export async function loadSessionSnapshot(
  sessionId: string,
  msgApi: ToastApi,
): Promise<boolean> {
  try {
    const snap = await fetchSession(sessionId)
    if (!snap) return false
    const dispatch = useAnalysisStore.getState().dispatch
    if (snap.current_requirement) {
      dispatch({
        type: 'requirement/received',
        requirement: snap.current_requirement.payload,
      })
    }
    for (const v of snap.session?.report_versions ?? []) {
      dispatch({ type: 'report/received', report: toReportVersion(v, sessionId) })
    }
    if (isAnalysisPhase(snap.session.phase)) {
      dispatch({ type: 'phase/received', phase: snap.session.phase })
    }
    return snap.session.phase === 'generating' || snap.session.phase === 'adjusting'
  } catch (err) {
    msgApi.error(`加载会话失败：${String(err).slice(0, 100)}`)
    return false
  }
}

/** 拉取最新版本列表并选中最后一个（onReport / 后台完成轮询后调用）。 */
export async function refreshVersionsAndSelectLatest(sessionId: string): Promise<void> {
  try {
    const snap = await fetchSession(sessionId)
    if (!snap) return
    const dispatch = useAnalysisStore.getState().dispatch
    for (const v of snap.session.report_versions) {
      dispatch({ type: 'report/received', report: toReportVersion(v, sessionId) })
    }
    if (snap.session.report_versions.length > 0) {
      const last = snap.session.report_versions[snap.session.report_versions.length - 1]
      dispatch({ type: 'report/selected', version: last.version })
    }
  } catch {
    /* ignore */
  }
}