import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconLogout } from '../components/ui/Icons'
import Avatar from '../components/atelier/Avatar'
import TopBar from '../components/atelier/TopBar'
import Dropdown from '../components/atelier/Dropdown'
import { useToast } from '../components/atelier/useToast'
import Composer from '../components/workbench/Composer'
import { UserBubble, AgentBubble } from '../components/workbench/MessageBubbles'
import WorkbenchEmpty from '../components/workbench/WorkbenchEmpty'
import ParsingCard from '../components/workbench/ParsingCard'
import ProgressCard from '../components/workbench/ProgressCard'
import ErrorCard from '../components/workbench/ErrorCard'
import RightRail from '../components/workbench/RightRail'
import SessionRail from '../components/workbench/SessionRail'
import {
  agentCopy,
  canvasKicker,
  chatModeForPhase,
  composerPlaceholder,
} from '../components/workbench/phaseText'
import { useAnalysisStore } from '../stores/analysisStore'
import { canRetryFailedAction, isBusyPhase } from '../stores/analysisReducer'
import { useAuthStore } from '../stores/authStore'
import { fetchSessions, fetchSession, patchRequirement, SESSIONS_PAGE_SIZE } from '../api/sessionsClient'
import { openChat } from '../api/analysisClient'
import { postConfirmStream, type ToastApi, type Dispatcher } from '../api/confirmStream'
import RequirementCardView from '../components/workbench/RequirementCardView'
import ReportPaper from '../components/workbench/ReportPaper'
import type { AnalysisPhase, ReportVersionStatus } from '../types/analysis'
import type { SessionSummary as ApiSessionSummary } from '../api/sessionsClient'
import type { RequirementCard as RC } from '../types/requirement'
import '../styles/global.css'
import '../styles/workbench.css'

const DONE_TIMEOUT_MS = 60_000
const EXEC_POLL_INTERVAL_MS = 5000

/**
 * 后台任务轮询：停止显示后每 5s 查一次 session snapshot，phase 离开
 * generating/adjusting（report_ready 或 error）时回调 onDone。
 * 「后台跑完」语义：停止只断前端渲染，任务继续跑完落库，轮询负责通知。
 */
export function useExecutionPoll(
  sessionId: string | null,
  active: boolean,
  onDone: () => void,
  intervalMs: number = EXEC_POLL_INTERVAL_MS,
): void {
  useEffect(() => {
    if (!active || !sessionId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        const snap = await fetchSession(sessionId)
        if (cancelled || !snap) return
        if (snap.session.phase === 'report_ready' || snap.session.phase === 'error') {
          onDone()
          return
        }
      } catch {
        /* 轮询失败静默，下一轮重试 */
      }
      if (!cancelled) timer = setTimeout(tick, intervalMs)
    }
    timer = setTimeout(tick, intervalMs)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [sessionId, active, onDone, intervalMs])
}

export default function WorkbenchPage() {
  const navigate = useNavigate()
  const phase = useAnalysisStore((s) => s.phase)
  const error = useAnalysisStore((s) => s.error)
  const dispatch = useAnalysisStore((s) => s.dispatch)
  const activeSessionId = useAnalysisStore((s) => s.activeSessionId)
  const sessions = useAnalysisStore((s) => s.sessions)
  const sessionsOffset = useAnalysisStore((s) => s.sessionsOffset)
  const hasMoreSessions = useAnalysisStore((s) => s.hasMoreSessions)
  const sessionsPageLoading = useAnalysisStore((s) => s.sessionsPageLoading)
  const requirement = useAnalysisStore((s) => s.requirement)
  const reportVersions = useAnalysisStore((s) => s.reportVersions)
  const selectedReportVersion = useAnalysisStore((s) => s.selectedReportVersion)
  const auth = useAuthStore()
  const toast = useToast()
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [composer, setComposer] = useState('')
  const [sending, setSending] = useState(false)
  const [patching, setPatching] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [execPolling, setExecPolling] = useState(false)
  const busy = isBusyPhase(phase)

  // 停止显示后轮询后台任务完成：phase 回 report_ready/error → 刷新版本 + 通知。
  useExecutionPoll(
    activeSessionId,
    execPolling,
    useCallback(() => {
      if (!activeSessionId) return
      void refreshVersionsAndSelectLatest(activeSessionId, dispatch)
      toast.success('报告已在后台生成，可查看')
      setExecPolling(false)
    }, [activeSessionId, dispatch, toast]),
  )

  // SessionSummary ← API 响应的字段映射；两处共用（useEffect + loadMore）。
  function mapSessions(raw: ApiSessionSummary[]) {
    return raw.map((s) => ({
      session_id: s.session_id,
      title: s.title ?? '',
      phase: ((s.phase ?? 'idle') as any),
      msg_count: s.msg_count ?? 0,
      updated_at: s.updated_at ?? '',
      report_versions: (s.report_versions ?? []).map((v: { version: number; title?: string; status?: string; created_at?: string; favorite?: boolean }) => ({
        version: v.version,
        title: v.title,
        status: v.status as any,
        created_at: v.created_at,
        favorite: v.favorite,
      })),
      first_message: s.first_message ?? '',
      last_message: s.last_message ?? '',
    } as any))
  }
  const [focusMode, setFocusMode] = useState(false)
  const [lastQuestion, setLastQuestion] = useState<string | null>(null)
  const composerRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFocusMode(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Smooth-scroll to the bottom when a report lands (prototype behavior).
  useEffect(() => {
    if (phase === 'report_ready' && scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight })
    }
  }, [phase, reportVersions.length])

  const [stageIndex, setStageIndex] = useState(0)
  const confirmAbortRef = useRef<AbortController | null>(null)

  // Prototype pacing: stage 0 (需求已确认) completes the moment the confirm
  // stream opens — the requirement lock is its entry condition. Stages 1-3
  // advance on a 650ms timer; the real report/error event ends the run.
  useEffect(() => {
    if (phase !== 'generating' && phase !== 'adjusting') return
    setStageIndex(1)
    let index = 1
    const timer = setInterval(() => {
      index = Math.min(3, index + 1)
      setStageIndex(index)
      if (index >= 3) clearInterval(timer)
    }, 650)
    return () => clearInterval(timer)
  }, [phase])

  function handleStop() {
    // 「后台跑完」语义：停止只断开本连接的渲染，后端任务继续跑到落库。
    confirmAbortRef.current?.abort()
    confirmAbortRef.current = null
    setConfirming(false)
    dispatch({ type: 'phase/received', phase: 'awaiting_confirm' })
    setExecPolling(true)
    toast.info('已停止显示，报告仍在后台生成，完成后将通知你')
  }

  useEffect(() => {
    let cancelled = false
    setSessionsLoading(true)
    fetchSessions(SESSIONS_PAGE_SIZE, 0)
      .then((res) => {
        if (!cancelled) {
          const mapped = mapSessions(res.sessions)
          // Plan B 步骤 2：分页。hasMore = 本次返回数 == page size（服务端可能还有）
          const hasMore = res.sessions.length === SESSIONS_PAGE_SIZE
          dispatch({ type: 'sessions/page-received', sessions: mapped, hasMore })
        }
      })
      .catch((err) => {
        if (!cancelled) toast.error(`会话列表加载失败：${String(err).slice(0, 100)}`)
      })
      .finally(() => {
        if (!cancelled) setSessionsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [dispatch, toast])

  // Plan B 步骤 2：loadMore — 点击 SessionRail 底部"加载更多"时触发
  const loadMoreSessions = useCallback(async () => {
    if (!hasMoreSessions || sessionsPageLoading) return
    dispatch({ type: 'sessions/page-loading', loading: true })
    try {
      const res = await fetchSessions(SESSIONS_PAGE_SIZE, sessionsOffset)
      const mapped = mapSessions(res.sessions)
      const hasMore = res.sessions.length === SESSIONS_PAGE_SIZE
      dispatch({ type: 'sessions/page-appended', sessions: mapped, hasMore })
    } catch (err) {
      toast.error(`加载更多会话失败：${String(err).slice(0, 100)}`)
      dispatch({ type: 'sessions/page-loading', loading: false })
    }
  }, [hasMoreSessions, sessionsPageLoading, sessionsOffset, dispatch, toast])

  function handleLogout() {
    auth.logout()
    toast.success('已退出登录')
    navigate('/login', { replace: true })
  }

  function handleNewAnalysis() {
    dispatch({ type: 'analysis/reset' })
    setComposer('')
    setLastQuestion(null)
  }

  function handleSend(textOverride?: string) {
    const text = (textOverride ?? composer).trim()
    if (!text || sending) return
    setSending(true)
    setComposer('')
    setLastQuestion(text)
    const sid = activeSessionId ?? crypto.randomUUID()
    if (!activeSessionId) {
      dispatch({ type: 'session/selected', sessionId: sid })
    }
    const mode = chatModeForPhase(phase)
    openChat(
      {
        user_query: text,
        mode,
        session_id: sid,
        base_report_version: mode === 'adjust' ? selectedReportVersion : undefined,
      },
      (evt) => handleSSEEvent(evt, toast, setSending),
    )
    setTimeout(() => {
      setSending((cur) => {
        if (cur) {
          toast.error('请求超时，请稍后重试')
          return false
        }
        return cur
      })
    }, DONE_TIMEOUT_MS)
  }

  function handleSelectSession(sessionId: string) {
    dispatch({ type: 'session/selected', sessionId })
    setLastQuestion(
      (sessions.find((s) => s.session_id === sessionId) as { first_message?: string } | undefined)
        ?.first_message ?? null,
    )
    void loadSessionSnapshot(sessionId, toast)
  }

  async function handlePatchAndConfirm(card: RC) {
    if (!activeSessionId) {
      toast.error('会话丢失，请重新开始')
      return
    }
    setPatching(true)
    try {
      const res = await patchRequirement(activeSessionId, card)
      const saved: RC = res.requirement
      dispatch({ type: 'requirement/received', requirement: saved })
      if (saved.status === 'complete') {
        setConfirming(true)
        const controller = new AbortController()
        confirmAbortRef.current = controller
        await postConfirmStream(
          activeSessionId,
          {
            toast,
            dispatch,
            setConfirming,
            onReport: (version) => {
              void refreshVersionsAndSelectLatest(activeSessionId, dispatch)
              toast.success(`报告 v${version ?? ''} 已生成并保留在当前会话`)
            },
          },
          'confirm',
          controller.signal,
        )
      } else {
        toast.info('PATCH 已保存，但仍有缺失字段未填')
      }
    } catch (err) {
      toast.error(`PATCH 失败：${String(err).slice(0, 200)}`)
    } finally {
      setPatching(false)
      setConfirming(false)
    }
  }

  async function handleRetry() {
    if (!activeSessionId) {
      toast.error('会话丢失，请重新开始')
      return
    }
    setConfirming(true)
    const controller = new AbortController()
    confirmAbortRef.current = controller
    try {
      await postConfirmStream(
        activeSessionId,
        {
          toast,
          dispatch,
          setConfirming,
          onReport: (version) => {
            void refreshVersionsAndSelectLatest(activeSessionId, dispatch)
            toast.success(`报告 v${version ?? ''} 已生成并保留在当前会话`)
          },
        },
        'retry',
        controller.signal,
      )
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className="workbench-shell">
      <TopBar brand="ReportAgent" subtitle="工作台">
        <nav className="atelier-topbar__nav">
          <button type="button" className="nav-btn is-active">工作台</button>
          <button type="button" className="nav-btn" onClick={() => navigate('/templates')}>
            模板中心
          </button>
          <button type="button" className="nav-btn" onClick={() => navigate('/observability')}>
            可观测
          </button>
        </nav>
        <div className="atelier-topbar__meta">
          <span
            className="atelier-live"
            style={busy ? { background: 'var(--amber)', boxShadow: 'none' } : undefined}
          />
          <span>{busy ? '处理中' : '已连接'}</span>
          <Dropdown
            items={[
              {
                key: 'logout',
                icon: <IconLogout />,
                label: '退出登录',
                onClick: handleLogout,
              },
            ]}
            placement="bottom-end"
          >
            <span
              style={{
                marginLeft: 8,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <Avatar size="sm">{auth.username?.[0]?.toUpperCase() ?? 'U'}</Avatar>
              <span>{auth.username ?? 'user'}</span>
              <span style={{ fontSize: 10 }}>▾</span>
            </span>
          </Dropdown>
        </div>
      </TopBar>

      <div className={focusMode ? 'workbench-body focus' : 'workbench-body'}>
        <SessionRail
          sessions={sessions}
          activeSessionId={activeSessionId}
          reportVersions={reportVersions}
          selectedReportVersion={selectedReportVersion}
          loading={sessionsLoading}
          onSelect={(sid) => handleSelectSession(sid)}
          onSelectVersion={(version) => dispatch({ type: 'report/selected', version })}
          onNew={() => {
            handleNewAnalysis()
            composerRef.current?.focus()
          }}
          hasMore={hasMoreSessions}
          loadingMore={sessionsPageLoading}
          onLoadMore={() => { void loadMoreSessions() }}
        />

        <main
          className="workbench-canvas"
          style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}
        >
          <div className="wb-canvas-head">
            <div>
              <div className="wb-canvas-kicker">{canvasKicker(phase)}</div>
              <div className="wb-canvas-title">
                {sessions.find((s) => s.session_id === activeSessionId)?.title || '新分析'}
              </div>
            </div>
            <div className="wb-head-actions">
              <button
                type="button"
                className="wb-quiet-btn"
                onClick={() => setFocusMode((current) => !current)}
              >
                {focusMode ? '退出聚焦' : '聚焦内容'}
              </button>
            </div>
          </div>

          <div className="wb-scroll" ref={scrollRef}>
            <div className="wb-content-inner">
          {lastQuestion && phase !== 'idle' && <UserBubble text={lastQuestion} />}
          {phase !== 'idle' && agentCopy(phase) && <AgentBubble markdown={agentCopy(phase)} />}
          {phase === 'parsing' && <ParsingCard />}

          {phase === 'error' && canRetryFailedAction({ phase, error }) && (
            <ErrorCard
              message={error?.message}
              kind={error?.kind}
              sql={error?.sql}
              onRetry={handleRetry}
              retrying={confirming}
            />
          )}
          {(phase === 'generating' || phase === 'adjusting') && (
            <ProgressCard
              adjusting={phase === 'adjusting'}
              stageIndex={stageIndex}
              onStop={handleStop}
            />
          )}

          {requirement && (
            <RequirementCardView
              card={requirement}
              onChange={(next) => dispatch({ type: 'requirement/received', requirement: next })}
              onConfirm={async () => {
                await handlePatchAndConfirm(requirement)
              }}
              onFocusComposer={() => composerRef.current?.focus()}
            />
          )}

          {activeSessionId && selectedReportVersion != null && (
            <ReportPaper
              key={`${activeSessionId}-${selectedReportVersion}`}
              sessionId={activeSessionId}
              version={selectedReportVersion}
              requirement={requirement}
              onAdjust={(text) => handleSend(text)}
              onFocusComposer={() => composerRef.current?.focus()}
            />
          )}

          {!requirement && reportVersions.length === 0 && phase === 'idle' && (
            <WorkbenchEmpty onPick={(text) => handleSend(text)} />
          )}
            </div>
          </div>

          <Composer
            ref={composerRef}
            value={composer}
            onChange={setComposer}
            onSubmit={() => handleSend()}
            disabled={busy || sending || patching || confirming}
            placeholder={composerPlaceholder(phase)}
          />
        </main>

        <RightRail
          phase={phase}
          requirement={requirement}
          onSuggest={(text) => {
            setComposer(text)
            composerRef.current?.focus()
          }}
        />
      </div>
    </div>
  )
}

function handleSSEEvent(
  evt: { type: string; data: any },
  msgApi: ToastApi,
  setSending: (v: boolean | ((cur: boolean) => boolean)) => void,
) {
  const dispatch = useAnalysisStore.getState().dispatch
  if (evt.type === 'phase') {
    dispatch({ type: 'phase/received', phase: evt.data.phase as AnalysisPhase })
  } else if (evt.type === 'requirement') {
    dispatch({ type: 'requirement/received', requirement: evt.data })
  } else if (evt.type === 'error') {
    msgApi.error(evt.data?.message ?? '处理失败')
    dispatch({ type: 'analysis/failed', error: evt.data })
  } else if (evt.type === 'done') {
    if (evt.data?.final_phase) {
      dispatch({ type: 'phase/received', phase: evt.data.final_phase as AnalysisPhase })
    }
  }
  if (evt.type === 'requirement' || evt.type === 'done' || evt.type === 'error') {
    setSending(false)
  }
}

async function loadSessionSnapshot(
  sessionId: string,
  msgApi: ToastApi,
) {
  try {
    const { fetchSession } = await import('../api/sessionsClient')
    const snap = await fetchSession(sessionId)
    if (!snap) return
    const dispatch = useAnalysisStore.getState().dispatch
    if (snap.current_requirement) {
      dispatch({
        type: 'requirement/received',
        requirement: snap.current_requirement.payload,
      })
    }
    if (snap.session?.report_versions) {
      for (const v of snap.session.report_versions) {
        const status = (['generating', 'done', 'error'] as ReportVersionStatus[]).includes(
          v.status as any,
        )
          ? (v.status as ReportVersionStatus)
          : 'done'
        dispatch({
          type: 'report/received',
          report: {
            id: `r-${v.version}`,
            session_id: sessionId,
            version: v.version,
            parent_version: v.version > 1 ? v.version - 1 : null,
            title: v.title,
            status,
            report: { answer: { text: '' } } as any,
            created_at: v.created_at,
          },
        })
      }
    }
  } catch (err) {
    msgApi.error(`加载会话失败：${String(err).slice(0, 100)}`)
  }
}

async function refreshVersionsAndSelectLatest(
  sessionId: string,
  _dispatch: Dispatcher,
) {
  try {
    const { fetchSession } = await import('../api/sessionsClient')
    const snap = await fetchSession(sessionId)
    if (!snap) return
    const dispatch = useAnalysisStore.getState().dispatch
    for (const v of snap.session.report_versions) {
      const status = (['generating', 'done', 'error'] as ReportVersionStatus[]).includes(
        v.status as any,
      )
        ? (v.status as ReportVersionStatus)
        : 'done'
      dispatch({
        type: 'report/received',
        report: {
          id: `r-${v.version}`,
          session_id: sessionId,
          version: v.version,
          parent_version: v.version > 1 ? v.version - 1 : null,
          title: v.title,
          status,
          report: { answer: { text: '' } } as any,
          created_at: v.created_at,
        },
      })
    }
    if (snap.session.report_versions.length > 0) {
      const last = snap.session.report_versions[snap.session.report_versions.length - 1]
      dispatch({ type: 'report/selected', version: last.version })
    }
  } catch {
    /* ignore */
  }
}

