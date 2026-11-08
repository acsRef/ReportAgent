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
import { isBusyPhase } from './analysisReducer'

export interface StreamEventCtx {
  msgApi: ToastApi
  sessionId: string | null
  setSending: (v: boolean | ((cur: boolean) => boolean)) => void
  /** P11：trace progress 帧实时回调——ProgressCard 真信号源。 */
  onTrace?: (entry: TimelineEntry) => void
}

export function handleSSEEvent(evt: AnalysisStreamEvent, ctx: StreamEventCtx): void {
  const dispatch = useAnalysisStore.getState().dispatch
  switch (evt.type) {
    case 'phase':
      dispatch({ type: 'phase/received', phase: evt.phase })
      // 2026-11-08（plan analysis-quality-cleanup D3）：确认后草稿已被后端锁定，
      // 但卡还是 confirm 前那份（status=complete）→ pill 一直显示「信息完整 · 待确认」，
      // 与「已按这份需求在跑」的实际状态自相矛盾。进入执行相位即视为已确认。
      if (evt.phase === 'generating' || evt.phase === 'adjusting') {
        dispatch({ type: 'requirement/locked' })
      }
      break
    case 'requirement':
      dispatch({ type: 'requirement/received', requirement: evt.requirement })
      ctx.setSending(false)
      break
    case 'trace':
      // 2026-11-04（plan trace-visible-reasoning）：trace 帧进**最后一条助手消息**的
      // 过程列表（2026-11-05 修订 A：过程挂在消息上，不再是全局 timeline）；
      // 同时回调给页面做进度卡。
      dispatch({ type: 'timeline/received', entry: evt.entry })
      ctx.onTrace?.(evt.entry)
      break
    case 'report': {
      const report = evt.report
      if (typeof report.version === 'number') {
        // P11 F5：adjust/retry 走 /chat 的 report——刷新版本列表并选中最新。
        dispatch({ type: 'chat/version', version: report.version })
        if (ctx.sessionId) void refreshVersionsAndSelectLatest(ctx.sessionId)
        ctx.msgApi.success(`报告 v${report.version} 已生成`)
      } else if (typeof report.answer?.text === 'string') {
        // P11 F4：chitchat 闲聊回复——写进当轮助手消息的正文（不进版本流）。
        dispatch({ type: 'chat/text', text: report.answer.text })
      }
      break
    }
    case 'error':
      ctx.msgApi.error(evt.error.message ?? '处理失败')
      dispatch({ type: 'analysis/failed', error: evt.error })
      // 这一轮助手消息定性为「失败」，过程收尾成 error
      dispatch({ type: 'chat/status', status: 'error' })
      dispatch({ type: 'timeline/closed', reason: 'error' })
      ctx.setSending(false)
      // Review-2 / 历史 known-red 修复：FAILED 版本落库后后端只发 error 事件
      // （P9/P10 出口收编），前端此前从不刷新版本列表 → ReportPaper 的
      // error band 永不渲染（spec 05）。宪法 §11 顺序 = persist state →
      // user-visible error，此处 error 到达时 FAILED 版本已在库——刷新并
      // 选中最新即可渲染 error band；纯拒绝类错误（无版本落库）刷新无副作用。
      if (ctx.sessionId) void refreshVersionsAndSelectLatest(ctx.sessionId)
      break
    case 'done':
      ctx.setSending(false)
      // 正常收尾：把仍在「进行中」的步骤收掉（避免面板永远停在 ◐）
      dispatch({ type: 'timeline/closed', reason: 'done' })
      dispatch({ type: 'chat/status', status: 'done' })
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
 * @returns true = 会话仍在后台忙（`parsing` / `generating` / `adjusting`）——调用方据此恢复轮询。
 *
 * 2026-11-08（plan interruption-resilience）：这里此前只认 generating/adjusting，
 * 于是**解析中按 F5** 回来后不会起轮询 → 界面永远停在「正在解析」（发送框一直禁用），
 * 而库里那一轮其实已经跑完。判定统一走 `isBusyPhase`（与轮询完成判定 `isSettledPhase` 同一张表）。
 */
export async function loadSessionSnapshot(
  sessionId: string,
  msgApi: ToastApi,
): Promise<boolean> {
  try {
    const snap = await fetchSession(sessionId)
    if (!snap) return false
    const dispatch = useAnalysisStore.getState().dispatch
    if (snap.messages) {
      dispatch({ type: 'chat/merged', messages: snap.messages })
    }
    if (snap.current_requirement) {
      dispatch({
        type: 'requirement/received',
        requirement: snap.current_requirement.payload,
      })
    }
    for (const v of snap.session?.report_versions ?? []) {
      dispatch({ type: 'report/received', report: toReportVersion(v, sessionId) })
    }
    const phase = snap.session.phase
    if (isAnalysisPhase(phase)) {
      dispatch({ type: 'phase/received', phase })
      return isBusyPhase(phase)
    }
    return false
  } catch (err) {
    msgApi.error(`加载会话失败：${String(err).slice(0, 100)}`)
    return false
  }
}

/**
 * 只刷新对话账本（一轮结束后调用，让刚结束的这轮进入历史时间线）。
 *
 * 2026-11-05（修订 A）：改成**合并**而不是整表替换——服务端历史按 id 去重后并入，
 * 屏幕上正在流的那条、闲聊回复都还在；刷新时机因此可以更随便（发送 / 需求帧 /
 * 报告帧 / 终态各拉一次都不会把界面刷空）。
 */
export async function refreshMessages(sessionId: string): Promise<void> {
  try {
    const snap = await fetchSession(sessionId)
    if (!snap?.messages) return
    useAnalysisStore.getState().dispatch({ type: 'chat/merged', messages: snap.messages })
  } catch {
    /* ignore：历史刷新失败不影响当前轮渲染 */
  }
}

/** 拉取最新版本列表并选中最后一个（onReport / 后台完成轮询后调用）。 */export async function refreshVersionsAndSelectLatest(sessionId: string): Promise<void> {
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