/**
 * Workbench page — three-column layout (TopBar + LeftRail + Center + RightRail).
 *
 * Flow:
 *   1. composer → /chat mode=new → SSE → requirement card
 *   2. card form (select/checkbox + assumption accept/reject)
 *   3. "确认执行" → PATCH /requirement → if complete, POST /confirm → SSE → report
 *   4. report v1 in center
 *
 * Strict hook usage: ALL `App.useApp()` calls happen inside the component
 * body; helpers that need `message` take it as a parameter.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  App,
  Avatar,
  Button,
  Dropdown,
  Empty,
  Input,
  Layout,
  List,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { DownOutlined, LogoutOutlined, PlusOutlined, UserOutlined } from '@ant-design/icons'
import { useAnalysisStore } from '../stores/analysisStore'
import { isBusyPhase } from '../stores/analysisReducer'
import { useAuthStore } from '../stores/authStore'
import {
  fetchSessions,
  patchRequirement,
  type SessionSummary as ApiSessionSummary,
} from '../api/sessionsClient'
import { openChat } from '../api/analysisClient'
import RequirementCardView from '../components/workbench/RequirementCardView'
import ReportPaper from '../components/workbench/ReportPaper'
import type { AnalysisPhase, ReportVersionStatus } from '../types/analysis'
import type { RequirementCard as RC } from '../types/requirement'
import '../styles/global.css'

const DONE_TIMEOUT_MS = 60_000

export default function WorkbenchPage() {
  const navigate = useNavigate()
  const phase = useAnalysisStore((s) => s.phase)
  const dispatch = useAnalysisStore((s) => s.dispatch)
  const activeSessionId = useAnalysisStore((s) => s.activeSessionId)
  const sessions = useAnalysisStore((s) => s.sessions)
  const requirement = useAnalysisStore((s) => s.requirement)
  const reportVersions = useAnalysisStore((s) => s.reportVersions)
  const selectedReportVersion = useAnalysisStore((s) => s.selectedReportVersion)
  const auth = useAuthStore()
  const { message } = App.useApp()
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [composer, setComposer] = useState('')
  const [sending, setSending] = useState(false)
  const [patching, setPatching] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const busy = isBusyPhase(phase)

  // Load sessions on mount
  useEffect(() => {
    let cancelled = false
    setSessionsLoading(true)
    fetchSessions()
      .then((res) => {
        if (!cancelled) {
          // Map API → store. The store's SessionSummary is strict on
          // `phase` / `status` literals; we coerce here.
          const mapped = res.sessions.map((s) => ({
            session_id: s.session_id,
            title: s.title,
            phase: ((s.phase ?? 'idle') as any),
            msg_count: s.msg_count,
            updated_at: s.updated_at,
            report_versions: s.report_versions.map((v) => ({
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
        if (!cancelled) message.error(`会话列表加载失败：${String(err).slice(0, 100)}`)
      })
      .finally(() => {
        if (!cancelled) setSessionsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [dispatch, message])

  function handleLogout() {
    auth.logout()
    message.success('已退出登录')
    navigate('/login', { replace: true })
  }

  function handleNewAnalysis() {
    dispatch({ type: 'analysis/reset' })
    setComposer('')
  }

  function handleSend(msgApi: MsgApi) {
    const text = composer.trim()
    if (!text || sending) return
    setSending(true)
    setComposer('')
    openChat(
      { user_query: text, mode: 'new', session_id: activeSessionId ?? undefined },
      (evt) => handleSSEEvent(evt, msgApi, setSending),
    )
    // Safety net: if no `done` event within DONE_TIMEOUT_MS, surface an
    // error and reset the spinner. The happy path resets on `done`.
    setTimeout(() => {
      setSending((cur) => {
        if (cur) {
          msgApi.error('请求超时，请稍后重试')
          return false
        }
        return cur
      })
    }, DONE_TIMEOUT_MS)
  }

  function handleSelectSession(sessionId: string) {
    dispatch({ type: 'session/selected', sessionId })
    void loadSessionSnapshot(sessionId, { error: message.error })
  }

  async function handlePatchAndConfirm(card: RC) {
    if (!activeSessionId) {
      message.error('会话丢失，请重新开始')
      return
    }
    setPatching(true)
    try {
      const res = await patchRequirement(activeSessionId, card)
      const saved: RC = res.requirement
      dispatch({ type: 'requirement/received', requirement: saved })
      if (saved.status === 'complete') {
        setConfirming(true)
        await postConfirmStream(
          activeSessionId,
          {
            message: {
              error: (m) => message.error(m),
              success: (m) => message.success(m),
              warning: (m) => message.warning(m),
            },
            dispatch,
            setConfirming,
            onReport: () => refreshVersionsAndSelectLatest(activeSessionId, dispatch),
          },
        )
      } else {
        message.info('PATCH 已保存，但仍有缺失字段未填')
      }
    } catch (err) {
      message.error(`PATCH 失败：${String(err).slice(0, 200)}`)
    } finally {
      setPatching(false)
    }
  }

  return (
    <div className="workbench-shell">
      {/* TopBar */}
      <Layout.Header
        style={{
          background: 'var(--ink)',
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          padding: '0 22px',
          fontFamily: 'var(--font-display)',
          letterSpacing: 0.3,
        }}
      >
        <Typography.Title
          level={4}
          style={{
            color: '#FFFFFF',
            margin: 0,
            fontFamily: 'var(--font-display)',
            fontSize: 17,
            fontWeight: 700,
          }}
        >
          ReportAgent
        </Typography.Title>
        <span
          style={{
            marginLeft: 14,
            color: 'rgba(255,255,255,.65)',
            fontFamily: 'var(--font-ui)',
            fontSize: 11,
            letterSpacing: 1.2,
            textTransform: 'uppercase',
          }}
        >
          工作台
        </span>
        <div style={{ flex: 1 }} />
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            color: 'rgba(255,255,255,.85)',
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
          menu={{
            items: [
              {
                key: 'logout',
                icon: <LogoutOutlined />,
                label: '退出登录',
                onClick: handleLogout,
              },
            ],
          }}
          placement="bottomRight"
        >
          <span
            style={{
              marginLeft: 18,
              color: '#FFFFFF',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontFamily: 'var(--font-ui)',
              fontSize: 12,
            }}
          >
            <Avatar size={24} icon={<UserOutlined />} style={{ background: 'var(--teal)' }} />
            {auth.username ?? 'user'}
            <DownOutlined style={{ fontSize: 10 }} />
          </span>
        </Dropdown>
      </Layout.Header>

      <div className="workbench-body">
        {/* LeftRail */}
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
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleNewAnalysis}
            style={{ fontWeight: 500 }}
          >
            新建分析
          </Button>

          <Typography.Text
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
          </Typography.Text>

          {sessionsLoading ? (
            <div style={{ textAlign: 'center', padding: 8 }}>
              <Spin size="small" />
            </div>
          ) : sessions.length === 0 ? (
            <Typography.Text style={{ color: 'var(--faint)', fontSize: 12 }}>
              暂无会话
            </Typography.Text>
          ) : (
            <List
              size="small"
              dataSource={sessions}
              renderItem={(s: ApiSessionSummary) => {
                const isActive = activeSessionId === s.session_id
                return (
                  <List.Item
                    onClick={() => handleSelectSession(s.session_id)}
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
                  </List.Item>
                )
              }}
            />
          )}

          <div style={{ flex: 1 }} />
          <Button
            type="link"
            onClick={() => navigate('/templates')}
            style={{ color: 'var(--ink-2)', padding: 0, justifyContent: 'flex-start' }}
          >
            模板中心 →
          </Button>
        </aside>

        {/* Center */}
        <main
          style={{
            padding: 'var(--sp-xl)',
            overflow: 'auto',
            background: 'var(--canvas)',
            display: 'flex',
            flexDirection: 'column',
            gap: 18,
          }}
        >
          <Typography.Title
            level={3}
            style={{
              fontFamily: 'var(--font-display)',
              color: 'var(--ink)',
              margin: 0,
            }}
          >
            新分析
          </Typography.Title>

          {/* Composer */}
          <div
            style={{
              background: 'var(--paper)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-m)',
              padding: 'var(--sp-l)',
              boxShadow: 'var(--shadow-soft)',
            }}
          >
            <Input.TextArea
              placeholder="用一句话描述你想分析的问题，例如：今年华东销售趋势"
              value={composer}
              onChange={(e) => setComposer(e.target.value)}
              autoSize={{ minRows: 2, maxRows: 6 }}
              disabled={sending}
              style={{ marginBottom: 12 }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography.Text style={{ color: 'var(--muted)', fontSize: 11 }}>
                当前 phase: <Tag color={phase === 'error' ? 'red' : phase === 'idle' ? 'default' : 'teal'}>{phase}</Tag>
              </Typography.Text>
              <Button
                type="primary"
                loading={sending}
                onClick={() => handleSend({
                  error: (m) => message.error(m),
                  success: (m) => message.success(m),
                  warning: (m) => message.warning(m),
                })}
                disabled={!composer.trim()}
              >
                提交分析
              </Button>
            </div>
          </div>

          {/* Requirement card */}
          {requirement && (
            <RequirementCardView
              card={requirement}
              onChange={(next) => dispatch({ type: 'requirement/received', requirement: next })}
              onConfirm={async () => {
                await handlePatchAndConfirm(requirement)
              }}
              confirming={patching || confirming}
            />
          )}

          {/* Report versions list */}
          {reportVersions.length > 0 && (
            <div
              style={{
                background: 'var(--paper)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-m)',
                padding: 'var(--sp-l)',
              }}
            >
              <Typography.Text
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
              </Typography.Text>
              <List
                size="small"
                dataSource={reportVersions}
                renderItem={(r) => (
                  <List.Item
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
                  </List.Item>
                )}
              />
            </div>
          )}

          {/* Selected report */}
          {activeSessionId && selectedReportVersion != null && (
            <ReportPaper
              key={`${activeSessionId}-${selectedReportVersion}`}
              sessionId={activeSessionId}
              version={selectedReportVersion}
            />
          )}

          {/* Empty state */}
          {!requirement && reportVersions.length === 0 && phase === 'idle' && (
            <div
              style={{
                background: 'var(--paper)',
                border: '1px dashed var(--line)',
                borderRadius: 'var(--r-m)',
                padding: 'var(--sp-2xl)',
              }}
            >
              <Empty
                description={
                  <Typography.Text style={{ color: 'var(--muted)' }}>
                    在上方输入框提出问题，agent 会自动生成需求卡
                  </Typography.Text>
                }
              />
            </div>
          )}
        </main>

        {/* RightRail: hint */}
        <aside
          className="workbench-rail workbench-rail--right"
          style={{
            background: 'var(--paper)',
            borderLeft: '1px solid var(--line)',
            padding: 'var(--sp-l)',
            overflow: 'auto',
          }}
        >
          <Typography.Text
            style={{
              fontSize: 10,
              letterSpacing: 1.4,
              color: 'var(--muted)',
              textTransform: 'uppercase',
              fontWeight: 700,
            }}
          >
            分析助手
          </Typography.Text>
          <Typography.Paragraph
            style={{ color: 'var(--ink-2)', fontSize: 12, marginTop: 12 }}
          >
            1. 提出问题 → 生成需求卡<br />
            2. 填写每个字段 + 接受/拒绝假设<br />
            3. 点击「确认执行」→ 报告版本出现在中央<br />
            4. 点版本号切换查看历史报告
          </Typography.Paragraph>
          <div style={{ marginTop: 16, fontSize: 11, color: 'var(--faint)' }}>
            会话已锁定到左栏选中的那条；切换会话会拉取 PG 快照。
          </div>
        </aside>
      </div>
    </div>
  )
}

// --- helpers (no React hooks; message + dispatch are passed in) -----

type MsgApi = { error: (m: string) => void; success: (m: string) => void; warning: (m: string) => void }
type Dispatcher = (a: any) => void

function handleSSEEvent(
  evt: { type: string; data: any },
  msgApi: MsgApi,
  setSending: (v: boolean | ((cur: boolean) => boolean)) => void,
) {
  // Read the current dispatch via getState (no subscription needed here).
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
  msgApi: { error: (m: string) => void },
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

async function postConfirmStream(
  sessionId: string,
  ctx: { message: MsgApi; dispatch: Dispatcher; setConfirming: (v: boolean) => void; onReport: () => void | Promise<void> },
) {
  // Read JWT
  const raw = localStorage.getItem('ragent_auth')
  const token = raw ? (JSON.parse(raw)?.state?.token ?? null) : null
  if (!token) {
    ctx.message.error('未登录')
    return
  }
  const dispatch = useAnalysisStore.getState().dispatch
  let res: Response
  try {
    res = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/confirm`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    })
  } catch (err) {
    ctx.message.error(`confirm 请求失败：${String(err).slice(0, 100)}`)
    return
  }
  if (!res.ok || !res.body) {
    ctx.message.error(`confirm 失败: ${res.status}`)
    dispatch({
      type: 'analysis/failed',
      error: { code: 'HTTP_ERROR', message: `status ${res.status}`, recoverable: false, failed_action: 'confirm' },
    })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let sawReport = false
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sepIndex
    while ((sepIndex = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      const evt = parseSSEFrame(frame)
      if (!evt) continue
      if (evt.eventName === 'phase') {
        dispatch({ type: 'phase/received', phase: evt.data.phase as AnalysisPhase })
      } else if (evt.eventName === 'report') {
        sawReport = true
        // The SSE report payload may be partial; fetch the persisted
        // version list to populate the store with the canonical row.
        await ctx.onReport()
        dispatch({ type: 'phase/received', phase: 'report_ready' })
      } else if (evt.eventName === 'error') {
        ctx.message.error(evt.data?.message ?? '执行失败')
        dispatch({ type: 'analysis/failed', error: evt.data })
      } else if (evt.eventName === 'done' && evt.data?.final_phase) {
        dispatch({ type: 'phase/received', phase: evt.data.final_phase as AnalysisPhase })
      }
    }
  }
  if (!sawReport) {
    ctx.message.warning('确认完成，但未收到报告事件')
  }
}

function parseSSEFrame(frame: string): { eventName: string; data: any } | null {
  let eventName: string | null = null
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!eventName) return null
  const dataStr = dataLines.join('\n')
  if (!dataStr) return null
  try {
    return { eventName, data: JSON.parse(dataStr) }
  } catch {
    return { eventName, data: dataStr }
  }
}
