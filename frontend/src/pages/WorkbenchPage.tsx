import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Text } from '../components/atelier/Typography'
import { LogoutOutlined, PlusOutlined } from '@ant-design/icons'
import Button from '../components/atelier/Button'
import Spinner from '../components/atelier/Spinner'
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
import {
  agentCopy,
  canvasKicker,
  chatModeForPhase,
  composerPlaceholder,
} from '../components/workbench/phaseText'
import { useAnalysisStore } from '../stores/analysisStore'
import { canRetryFailedAction, isBusyPhase } from '../stores/analysisReducer'
import { useAuthStore } from '../stores/authStore'
import {
  fetchSessions,
  patchRequirement,
  type SessionSummary as ApiSessionSummary,
} from '../api/sessionsClient'
import { openChat } from '../api/analysisClient'
import { postConfirmStream, type ToastApi, type Dispatcher } from '../api/confirmStream'
import RequirementCardView from '../components/workbench/RequirementCardView'
import ReportPaper from '../components/workbench/ReportPaper'
import type { AnalysisPhase, ReportVersionStatus } from '../types/analysis'
import type { RequirementCard as RC } from '../types/requirement'
import '../styles/global.css'
import '../styles/workbench.css'

const DONE_TIMEOUT_MS = 60_000

export default function WorkbenchPage() {
  const navigate = useNavigate()
  const phase = useAnalysisStore((s) => s.phase)
  const error = useAnalysisStore((s) => s.error)
  const dispatch = useAnalysisStore((s) => s.dispatch)
  const activeSessionId = useAnalysisStore((s) => s.activeSessionId)
  const sessions = useAnalysisStore((s) => s.sessions)
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
  const busy = isBusyPhase(phase)
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
    confirmAbortRef.current?.abort()
    confirmAbortRef.current = null
    setConfirming(false)
    toast.info('已停止生成，当前需求已保留')
    dispatch({ type: 'phase/received', phase: 'awaiting_confirm' })
  }

  useEffect(() => {
    let cancelled = false
    setSessionsLoading(true)
    fetchSessions()
      .then((res) => {
        if (!cancelled) {
          const mapped = res.sessions.map((s) => ({
            session_id: s.session_id,
            title: s.title ?? '',
            phase: ((s.phase ?? 'idle') as any),
            msg_count: s.msg_count ?? 0,
            updated_at: s.updated_at ?? '',
            report_versions: (s.report_versions ?? []).map((v) => ({
              version: v.version,
              title: v.title,
              status: v.status as any,
              created_at: v.created_at,
              favorite: v.favorite,
            })),
            first_message: s.first_message,
            last_message: s.last_message,
          } as any))
          dispatch({ type: 'sessions/received', sessions: mapped })
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
        <div style={{ flex: 1 }} />
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            color: 'var(--on-ink-2)',
            fontSize: 12,
            fontFamily: 'var(--font-ui)',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: busy ? 'var(--amber)' : 'var(--green)',
              display: 'inline-block',
            }}
          />
          {busy ? '处理中' : '已连接'}
        </span>
        <Dropdown
          items={[
            {
              key: 'logout',
              icon: <LogoutOutlined />,
              label: '退出登录',
              onClick: handleLogout,
            },
          ]}
          placement="bottom-end"
        >
          <span
            style={{
              marginLeft: 18,
              color: 'var(--on-ink)',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontFamily: 'var(--font-ui)',
              fontSize: 12,
            }}
          >
            <Avatar size="sm">{auth.username?.[0]?.toUpperCase() ?? 'U'}</Avatar>
            {auth.username ?? 'user'}
            <span style={{ fontSize: 10 }}>▾</span>
          </span>
        </Dropdown>
      </TopBar>

      <div className={focusMode ? 'workbench-body focus' : 'workbench-body'}>
        <aside
          className="workbench-rail workbench-rail--left"
          style={{
            background: 'var(--rail)',
            borderRight: '1px solid var(--line)',
            padding: 'var(--sp-l)',
            overflow: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          <Button
            variant="primary"
            block
            onClick={handleNewAnalysis}
            style={{ fontWeight: 600, height: 36 }}
          >
            <PlusOutlined /> 新建分析
          </Button>

          <Text
            style={{
              fontSize: 10,
              letterSpacing: 1.4,
              color: 'var(--muted)',
              textTransform: 'uppercase',
              fontWeight: 700,
              marginTop: 6,
            }}
          >
            最近会话
          </Text>

          {sessionsLoading ? (
            <div style={{ textAlign: 'center', padding: 8 }}>
              <Spinner size="sm" />
            </div>
          ) : sessions.length === 0 ? (
            <Text style={{ color: 'var(--faint)', fontSize: 12 }}>
              暂无会话
            </Text>
          ) : (
            <SessionListBuckets
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelect={(sid) => handleSelectSession(sid)}
            />
          )}

          <div style={{ flex: 1 }} />
          <Button
            variant="quiet"
            onClick={() => navigate('/templates')}
            style={{ color: 'var(--ink-2)', padding: 0, justifyContent: 'flex-start' }}
          >
            模板中心 →
          </Button>
        </aside>

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
            <ErrorCard message={error?.message} onRetry={handleRetry} retrying={confirming} />
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

          {reportVersions.length > 0 && (
            <div
              style={{
                background: 'var(--paper)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-m)',
                padding: 'var(--sp-l)',
              }}
            >
              <Text
                style={{
                  fontSize: 10,
                  letterSpacing: 1.4,
                  color: 'var(--muted)',
                  textTransform: 'uppercase',
                  fontWeight: 700,
                  marginBottom: 8,
                  display: 'block',
                }}
              >
                报告版本
              </Text>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {reportVersions.map((r) => (
                  <div
                    key={r.version}
                    onClick={() => dispatch({ type: 'report/selected', version: r.version })}
                    style={{
                      cursor: 'pointer',
                      padding: '6px 10px',
                      borderRadius: 6,
                      background: selectedReportVersion === r.version ? 'var(--teal-pale)' : 'transparent',
                    }}
                  >
                    <span style={{ color: 'var(--ink-2)', fontSize: 13, fontWeight: 500 }}>
                      v{r.version} · {r.title}
                    </span>
                    <span style={{ marginLeft: 12, fontSize: 11, color: 'var(--muted)' }}>
                      {r.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeSessionId && selectedReportVersion != null && (
            <ReportPaper
              key={`${activeSessionId}-${selectedReportVersion}`}
              sessionId={activeSessionId}
              version={selectedReportVersion}
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

function SessionListBuckets({
  sessions,
  activeSessionId,
  onSelect,
}: {
  sessions: ApiSessionSummary[]
  activeSessionId: string | null
  onSelect: (sid: string) => void
}) {
  const [showOlder, setShowOlder] = useState(false)
  const now = Date.now()
  const day = 24 * 60 * 60 * 1000

  const today: ApiSessionSummary[] = []
  const pastWeek: ApiSessionSummary[] = []
  const older: ApiSessionSummary[] = []
  for (const s of sessions) {
    const t = s.updated_at ? Date.parse(s.updated_at) : NaN
    const age = isFinite(t) ? (now - t) / day : Infinity
    if (age < 1) today.push(s)
    else if (age < 7) pastWeek.push(s)
    else older.push(s)
  }

  const renderItem = (s: ApiSessionSummary) => {
    const isActive = activeSessionId === s.session_id
    return (
      <div
        className="session-row"
        data-session-id={s.session_id}
        onClick={() => onSelect(s.session_id)}
        style={{
          padding: '8px 10px',
          borderRadius: 6,
          cursor: 'pointer',
          background: isActive ? 'var(--teal-pale)' : 'transparent',
          border: isActive ? '1px solid var(--teal)' : '1px solid transparent',
          marginBottom: 4,
        }}
      >
        <div
          style={{
            fontSize: 12,
            color: 'var(--ink-2)',
            fontWeight: isActive ? 600 : 400,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {s.first_message || s.session_id.slice(0, 8)}
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)' }}>
          {s.msg_count} 条 · {s.phase}
        </div>
      </div>
    )
  }

  const headerStyle: React.CSSProperties = {
    fontSize: 10,
    letterSpacing: 1.4,
    color: 'var(--muted)',
    textTransform: 'uppercase',
    fontWeight: 700,
    marginTop: 8,
    marginBottom: 4,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  }

  return (
    <div style={{ overflow: 'auto' }}>
      {today.length > 0 && (
        <>
          <div style={headerStyle}><span>今天</span><span style={{ color: 'var(--faint)' }}>{today.length}</span></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {today.map((s) => <div key={s.session_id}>{renderItem(s)}</div>)}
          </div>
        </>
      )}
      {pastWeek.length > 0 && (
        <>
          <div style={headerStyle}><span>过去 7 天</span><span style={{ color: 'var(--faint)' }}>{pastWeek.length}</span></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {pastWeek.map((s) => <div key={s.session_id}>{renderItem(s)}</div>)}
          </div>
        </>
      )}
      {older.length > 0 && (
        <>
          <div
            style={{ ...headerStyle, cursor: 'pointer' }}
            onClick={() => setShowOlder((s) => !s)}
          >
            <span>{showOlder ? '▾ 更早' : '▸ 更早'}</span>
            <span style={{ color: 'var(--faint)' }}>{older.length}</span>
          </div>
          {showOlder && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {older.map((s) => <div key={s.session_id}>{renderItem(s)}</div>)}
            </div>
          )}
        </>
      )}
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

