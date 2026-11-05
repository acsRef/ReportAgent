import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconLogout } from '../components/ui/Icons'
import Avatar from '../components/atelier/Avatar'
import TopBar from '../components/atelier/TopBar'
import Dropdown from '../components/atelier/Dropdown'
import { useToast } from '../components/atelier/useToast'
import Composer from '../components/workbench/Composer'
import AssistantTurn from '../components/workbench/AssistantTurn'
import { UserBubble } from '../components/workbench/MessageBubbles'
import WorkbenchEmpty from '../components/workbench/WorkbenchEmpty'
import ParsingCard from '../components/workbench/ParsingCard'
import ProgressCard from '../components/workbench/ProgressCard'
import TracePanel from '../components/workbench/TracePanel'
import ErrorCard from '../components/workbench/ErrorCard'
import RightRail from '../components/workbench/RightRail'
import SessionRail from '../components/workbench/SessionRail'
import {
  agentCopy,
  canvasKicker,
  chatModeForPhase,
  composerPlaceholder,
  settlePhaseFor,
} from '../components/workbench/phaseText'
import { fetchSessions, fetchSession, patchRequirement, SESSIONS_PAGE_SIZE } from '../api/sessionsClient'
import type { SessionSummary as ApiSessionSummary } from '../api/sessionsClient'
import { openChat } from '../api/analysisClient'
import { postConfirmStream } from '../api/confirmStream'
import { armStream, abortStream } from '../api/streamAbort'
import { stageFromTrace, liveDetailFromEntry } from '../components/workbench/progressModel'
import { createSendTimeoutGuard } from '../utils/sendTimeout'
import {
  handleSSEEvent,
  loadSessionSnapshot,
  refreshMessages,
  refreshVersionsAndSelectLatest,
} from '../stores/sessionEvents'
import { useAnalysisStore } from '../stores/analysisStore'
import { liveTrace, liveTraceNote } from '../stores/chatTranscript'
import { canRetryFailedAction, isBusyPhase, isSettledPhase } from '../stores/analysisReducer'
import { useAuthStore } from '../stores/authStore'
import type { TimelineEntry } from '../types/report'
import type { RequirementCard as RC } from '../types/requirement'
import QuestionBar from '../components/workbench/QuestionBar'
import ConfirmBar from '../components/workbench/ConfirmBar'
import { pickNextQuestion, ASSUMPTION_KEY_PREFIX, requirementUiMode } from '../components/workbench/slotQuestion'
import ReportPaper from '../components/workbench/ReportPaper'
import ConversationTimeline from '../components/workbench/ConversationTimeline'
import { buildChatTurns, currentTurnKey } from '../components/workbench/historyTurns'
import '../styles/global.css'
import '../styles/workbench.css'

const DONE_TIMEOUT_MS = 60_000
const EXEC_POLL_INTERVAL_MS = 5000

/**
 * 后台任务轮询：停止显示 / 流中断后每 5s 查一次 session snapshot，phase 离开
 * 「忙」（parsing / generating / adjusting）时回调 onDone。
 * 「后台跑完」语义：停止只断前端渲染，任务继续跑完落库，轮询负责通知。
 *
 * 2026-11-03：完成判定从「report_ready|error」改成 `isSettledPhase`——解析轮
 * （parsing）现在也后台化，只认报告相位会让「停止等待后把新卡刷回来」失效。
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
        if (isSettledPhase(snap.session.phase)) {
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
  const messages = useAnalysisStore((s) => s.messages)
  // 2026-11-04：执行过程（trace 步骤 + 异常说明）——2026-11-05 修订 A：挂在
  // 最后一条助手消息上（过程属于那条回复），不再是 store 里的全局 timeline。
  const timeline = useMemo(() => liveTrace(messages), [messages])
  const timelineNote = useMemo(() => liveTraceNote(messages), [messages])
  const auth = useAuthStore()
  const toast = useToast()
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [composer, setComposer] = useState('')
  const [sending, setSending] = useState(false)
  const [patching, setPatching] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [execPolling, setExecPolling] = useState(false)
  // 当前轮开始时的版本号：当前轮的报告只显示「这次新产生的版本」，
  // 否则新提问下面会吊着上一轮的旧报告（2026-11-02 对话时间线）。
  const [turnBaseVersion, setTurnBaseVersion] = useState<number | null>(null)
  /**
   * 2026-11-05（用户：「接着上一份报告改，发出去什么都没发生」→ 后来又说
   * 「你在这里生成卡片、生成这种东西干什么」）：**不自动确认、不自动跑 SQL**，
   * 只在解析出完整需求时把确认条滚进视野，由用户点「生成报告」。
   */
  const autoConfirmRef = useRef(false)
  // 选项提交（PATCH）进行中——防止连点造成两次 PATCH。
  const [answering, setAnswering] = useState(false)
  const refreshedTurnRef = useRef<string>('')
  const busy = isBusyPhase(phase)

  // 需求已确认并开始执行（或已出报告）→ 需求区不再出编辑动作。
  // 2026-11-05 修订 A：执行中（generating/adjusting）不再渲染只读整卡——
  // 那一轮的信息就是助手消息里的进度，卡片只是噪音。
  const cardLocked =
    requirement?.status === 'locked' || phase === 'generating' || phase === 'adjusting'
  // ── 需求澄清的呈现时机（2026-11-04，plan chat-like-conversation-flow）────────
  // 判定集中在 `requirementUiMode`：只有 awaiting_* 相位才出提问条/确认条；
  // parsing 时上一轮的条必须消失（否则误点 → 409 SESSION_BUSY），report_ready 也不出条。
  const requirementMode = requirementUiMode(phase, requirement)
  const questionField =
    requirementMode === 'question' && !cardLocked ? pickNextQuestion(requirement) : null
  const showConfirmBar = requirementMode === 'confirm' && !cardLocked
  // 有没有「过程」可展示：没有 trace 也不渲染那一行（否则是个空气泡）。
  const hasTrace = timeline.length > 0 || Boolean(timelineNote)

  // 2026-11-05（用户：「你在这里生成卡片、生成这种东西干什么」）：
  // **不自动确认、不自动跑 SQL**。上一版为了修「无法继续调整」做成了自动确认，
  // 结果是用户没点确认就冒出报告——比「没反应」更糟。
  // 现在：解析出完整卡就把确认条滚到视野里，由用户决定要不要生成。
  useEffect(() => {
    if (!autoConfirmRef.current) return
    if (phase !== 'awaiting_confirm' || !requirement) return
    autoConfirmRef.current = false
    const node = document.querySelector('[aria-label="需求确认"]')
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [phase, requirement])

  // ── 历史轮次时间线（2026-11-02）────────────────────────────────────────────  // 一轮结束后刷新 messages，让刚结束的这轮进入历史；用 (session, 版本数, phase)
  // 做幂等键，避免同一轮重复请求。
  useEffect(() => {
    if (!activeSessionId) return
    if (phase !== 'report_ready' && phase !== 'error') return
    const key = `${activeSessionId}:${reportVersions.length}:${phase}`
    if (refreshedTurnRef.current === key) return
    refreshedTurnRef.current = key
    void refreshMessages(activeSessionId)
  }, [activeSessionId, phase, reportVersions.length])

  // 历史 = 除最后一轮以外的所有轮次；问题与版本按下标配对（v1↔第 1 轮）。
  // 注：依赖 lastQuestion，故必须声明在 lastQuestion 之后（见下方 useMemo）。
  // 停止显示后轮询后台任务完成：phase 不再 busy → 拉快照（新需求卡 / 新版本 / 真实
  // phase 一并回填）+ 通知。2026-11-03：解析轮也走这里——「停止等待」或流中断之后，
  // 后台解析跑完落库，轮询把新卡刷回来（此前只刷版本，卡片永远停在旧内容）。
  useExecutionPoll(
    activeSessionId,
    execPolling,
    useCallback(() => {
      if (!activeSessionId) return
      void loadSessionSnapshot(activeSessionId, toast).then(() => {
        // 2026-11-04：后台补齐完成——过程面板补一条收尾步骤，清掉中断/停止说明
        dispatch({
          type: 'timeline/received',
          entry: {
            id: `background-done-${Date.now()}`,
            nodeName: '后台执行完成',
            status: 'success',
            kind: 'agent',
            timestamp: Date.now(),
          },
        })
        dispatch({ type: 'timeline/closed', reason: 'done' })
        toast.success('后台任务已完成，内容已更新')
      })
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

  // ── 对话账本 → 轮次（唯一数据源，2026-11-05 修订 A）────────────────────────
  // 历史与当轮都从 `messages` 派生：当轮 = 最后一个提问那一轮（phase=idle 时没有当轮）。
  // 不再有 `lastQuestion` 这种「页面自己记一句」的第二份状态——发送即 append，
  // 提问就在账本里，所以发下一句时上一轮不会被当成新的历史轮重新配对。
  const turns = useMemo(() => buildChatTurns(messages, reportVersions), [messages, reportVersions])
  const liveKey = useMemo(() => currentTurnKey(messages, phase), [messages, phase])
  const historyTurns = useMemo(
    () => (liveKey ? turns.filter((turn) => turn.key !== liveKey) : turns),
    [turns, liveKey],
  )
  const currentTurn = useMemo(
    () => (liveKey ? turns.find((turn) => turn.key === liveKey) ?? null : null),
    [turns, liveKey],
  )

  const composerRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // P-17：单次发送的「响应过慢」提示闸（只提示，不改 sending / 不断流）。
  const sendTimeoutRef = useRef(createSendTimeoutGuard(DONE_TIMEOUT_MS))

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFocusMode(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 2026-11-04：离开工作台（路由跳转 / 登出 / 关闭标签页）时断掉本方 SSE。
  // 后端任务是独立后台任务，会继续跑完落库；不主动断的话，离开后的帧会打在
  // 已经不存在的界面上（React 已经卸载，但网络请求还在）。
  useEffect(
    () => () => {
      abortStream()
    },
    [],
  )

  // Smooth-scroll to the bottom when a report lands (prototype behavior).
  useEffect(() => {
    if (phase === 'report_ready' && scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight })
    }
  }, [phase, reportVersions.length])

  const [stageIndex, setStageIndex] = useState(0)
  // P11：progress 由真实 trace 事件驱动——liveDetail 显示当前步骤文案。
  const [liveDetail, setLiveDetail] = useState<string | undefined>(undefined)
  // P11 Review-1 P1-2：confirm/retry 与 adjust(/chat openChat) 三条路径共用 armStream/abortStream，
  // 停止按钮能真断所有活跃 SSE；先前只有 confirm/retry 写入 controller，
  // adjust 走 openChat 的 controller 没保存，「停止」对 adjust 无效。
  // streamAbortRef 删除——改用模块级单例（src/api/streamAbort）便于单测 + 三流共用。

  // P11 D5：trace progress 帧 → stage（单调不减）+ live 文案。两个流
  // （/confirm 的 postConfirmStream 与 /chat 的 openChat）共用。
  function handleProgress(entry: TimelineEntry) {
    setStageIndex((cur) => stageFromTrace(entry.kind, entry.status, cur))
    setLiveDetail(liveDetailFromEntry(entry))
  }

  function handleStop() {
    // 「后台跑完」语义：停止只断开本连接的渲染，后端任务继续跑到落库。
    abortStream()
    sendTimeoutRef.current.clear()
    setConfirming(false)
    // P11 Review-1 P1-2：adjust 路径先前 stop 不 setSending(false)——UI 卡在「处理中」
    // 直到 60s timeout；同时停止必须同步关掉 streaming 标记，否则确认按钮被锁。
    setSending(false)
    dispatch({ type: 'phase/received', phase: 'awaiting_confirm' })
    // 执行过程面板：把进行中的步骤标「已停止」+ 说明后台仍在跑（2026-11-04）
    dispatch({ type: 'timeline/closed', reason: 'stopped' })
    setExecPolling(true)
    toast.info('已停止显示，报告仍在后台生成，完成后将通知你')
  }

  /**
   * 解析中停止等待（2026-11-03）。
   *
   * parsing 此前没有任何出口：发送框被 busy 锁死、没有停止按钮，用户只能刷新页面——
   * 而刷新会把当时还在 SSE 响应任务里的解析一起取消，结果既没落库也没渲染
   * （hand-off P0）。现在解析轮后台化，停止只断渲染：相位回到最后一张卡的稳定态，
   * 轮询在后台跑完后把新卡刷回来。
   */
  function handleStopParsing() {
    abortStream()
    sendTimeoutRef.current.clear()
    setSending(false)
    dispatch({
      type: 'phase/received',
      phase: settlePhaseFor(useAnalysisStore.getState().requirement),
    })
    dispatch({ type: 'timeline/closed', reason: 'stopped' })
    setExecPolling(true)
    toast.info('已停止等待，需求解析在后台继续，出结果后会自动更新')
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
    // 2026-11-04：退出/离开前断掉本方连接的 SSE（后台任务继续跑并由后端落库），
    // 避免登出后仍有事件写进已清空的 store。
    abortStream()
    dispatch({ type: 'analysis/reset' })
    auth.logout()
    toast.success('已退出登录')
    navigate('/login', { replace: true })
  }

  function handleNewAnalysis() {
    // 切到「新分析」= 不再观看上一轮：断流 + 清过程，后端任务照常跑完落库
    abortStream()
    dispatch({ type: 'analysis/reset' })
    setComposer('')
    setLiveDetail(undefined)
    setTurnBaseVersion(null)
    // P11 Review-1 P2：「新分析」必须显式清 execPolling——同 P1 残留，
    // 否则旧会话残留轮询会与新 session 重叠。
    setExecPolling(false)
  }

  function handleSend(textOverride?: string) {
    const text = (textOverride ?? composer).trim()
    if (!text || sending) return
    setSending(true)
    setComposer('')
    setStageIndex(1)
    setLiveDetail(undefined)
    setTurnBaseVersion(selectedReportVersion)
    // 2026-11-05：**不自动确认**（用户：「你在这里生成卡片、生成这种东西干什么」）。
    // 这里只记「本轮是接着报告改的」，用于把解析出来的确认条滚进视野。
    autoConfirmRef.current = phase === 'report_ready'
    const sid = activeSessionId ?? crypto.randomUUID()
    if (!activeSessionId) {
      // 新会话：先重置（会清空 messages），再追加本轮——顺序不能反。
      dispatch({ type: 'session/selected', sessionId: sid })
    }
    // 2026-11-05（plan conversation-transcript-messages 修订 A2）：**发送即 append**
    // ——用户那句 + 空的助手占位一次进账本。此后 SSE 只更新最后一条助手消息，
    // 界面上这一轮从第一秒起就是对话里的一轮，不再等「一轮结束后整体刷新」。
    dispatch({
      type: 'chat/turn-appended',
      turn: {
        userId: `u-${crypto.randomUUID()}`,
        assistantId: `a-${crypto.randomUUID()}`,
        text,
        createdAt: new Date().toISOString(),
      },
    })
    const mode = chatModeForPhase(phase)
    // 2026-11-05（修订 A）：**不再**在发送瞬间拉会话消息。此前那句「早拉一次保证上一句
    // 进入历史」在 append-only 账本里是多余的（本地已经追加了），而且新会话的服务端行
    // 还没创建 → 每次都打一个 404。历史由 requirement / 终态两次刷新按 id 合并回来。
    // P11 Review-1 P1-2：openChat 返回 AbortController——必须 armStream 注册到
    // 单例，否则 adjust 模式下「停止」按钮实际无效（SSE 继续收事件）。
    const controller = openChat(
      {
        user_query: text,
        mode,
        session_id: sid,
        base_report_version: mode === 'adjust' ? selectedReportVersion : undefined,
      },
      (evt) => {
        // 2026-11-04：**串台防护**——用户在上一轮还没跑完时切了会话/新建分析，
        // 这条流的事件如果继续写进 store，会把旧会话的结果画到新会话界面上。
        if (useAnalysisStore.getState().activeSessionId !== sid) return
        // 终态到达即撤掉「响应过慢」提示闸——否则它的 timer 会误伤后续某一轮。
        if (evt.type === 'done' || evt.type === 'error') sendTimeoutRef.current.clear()
        handleSSEEvent(evt, {
          msgApi: toast,
          sessionId: sid,
          setSending,
          onTrace: handleProgress,
        })
        // 2026-11-05（用户：「我说了啥都记录下来了，为什么不显示」）：
        // 需求轮落地（requirement 帧）和终态（report/error/done）各拉一次会话消息，
        // 把服务端 id 与 assistant_text 认领回本地那条消息（合并去重、不覆盖）。
        if (
          evt.type === 'requirement' ||
          evt.type === 'report' ||
          evt.type === 'error' ||
          evt.type === 'done'
        ) {
          void refreshMessages(sid)
        }
      },
      undefined,
      // 2026-11-03：流「静默结束」（无 requirement/report/error/done）时前端自己
      // 兜底——解锁发送框 + 诚实报错 + 转后台跟随。此前这种结束没有任何回调，
      // 发送框永久禁用、phase 停在 parsing，用户只能刷新（那会把解析一起取消）。
      ({ sawTerminal, aborted }) => {
        if (sawTerminal || aborted) return
        if (useAnalysisStore.getState().activeSessionId !== sid) return
        sendTimeoutRef.current.clear()
        setSending(false)
        dispatch({
          type: 'analysis/failed',
          error: {
            code: 'STREAM_INTERRUPTED',
            message: '连接中断，未收到本轮解析结果；解析可能在后台继续，稍后会自动更新',
            recoverable: true,
            failed_action: mode === 'legacy' ? 'new' : mode,
            kind: null,
            sql: null,
          },
        })
        // 执行过程面板：把进行中的步骤标成「中断」，并给出说明（不是静默转圈）
        dispatch({ type: 'timeline/closed', reason: 'interrupted' })
        dispatch({ type: 'chat/status', status: 'error' })
        setExecPolling(true)
      },
    )
    armStream(controller)
    // P-17：这里**只提示**，不改 sending、不 abort 流。此前裸 setTimeout 会在 60s 后
    // 弹「请求超时」并解锁发送框，但 SSE 仍在跑 —— 用户于是看到「说超时了结果又出来了」，
    // 而且解锁后可重复提交。任务终态由 done / error 收尾。
    sendTimeoutRef.current.arm(() => {
      toast.error('响应时间较长，任务仍在处理中；结果到达后会自动显示')
    })
  }

  function handleSelectSession(sessionId: string) {
    // 2026-11-04：切会话 = 不再观看上一轮。必须**先断掉当前流**，否则上一个会话的
    // 事件会继续写进新会话的界面（串台）。后台任务照常跑完落库，切回来由快照/轮询补齐。
    abortStream()
    dispatch({ type: 'session/selected', sessionId })
    setTurnBaseVersion(null)
    setLiveDetail(undefined)
    // P11 F6：恢复快照（requirement/版本/phase）；busy 会话（generating/adjusting）
    // 恢复后台轮询——停止后重进会话仍能收到「后台跑完」通知。
    // P11 Review-1 P2：始终 setExecPolling(busy)——非 busy 时同步 false，
    // 否则旧会话残留的 execPolling=true 会让 useExecutionPoll 误轮询新会话
    // 并在 phase=report_ready/error 时错误地触发「报告已在后台生成」通知。
    // 2026-11-05 修订 A：提问/历史全部来自快照里的对话账本（合并进 store），
    // 不再需要用 `last_message` 猜「当轮是哪句」。
    void loadSessionSnapshot(sessionId, toast).then((busy) => {
      setExecPolling(busy)
      // 2026-11-04：切回来时若该会话仍在后台跑，明确告诉用户在等什么
      // （否则界面像「卡住了」——过程面板此时是空的，因为 trace 只在内存里）。
      if (busy) {
        dispatch({
          type: 'timeline/note',
          note: '（重连）该会话有任务正在后台执行，完成后会自动更新',
        })
      }
    })
  }

  /**
   * 回答一个澄清槽位（2026-11-04，Phase 5）：选完立刻 PATCH，由**服务端**算新卡，
   * 于是下一个问题来自真实的槽位状态（而不是前端猜「还差什么」）。
   *
   * 为什么不只 dispatch 本地状态：`patch_requirement` 才会把 `metric:` / `intent:`
   * 前缀确定性翻译成卡体字段并重算 status——本地改 selected_value 只是表单态。
   */
  async function handleAnswerQuestion(key: string, value: string | string[]) {
    if (!requirement || !activeSessionId || answering) return

    // 假设也是问题：accept = 就按这个；revise = 交给输入框让用户自己说
    if (key.startsWith(ASSUMPTION_KEY_PREFIX)) {
      if (value === 'revise') {
        composerRef.current?.focus()
        return
      }
      const assumptionKey = key.slice(ASSUMPTION_KEY_PREFIX.length)
      const next: RC = {
        ...requirement,
        assumptions: requirement.assumptions.map((assumption) =>
          assumption.key === assumptionKey ? { ...assumption, accepted: true } : assumption,
        ),
      }
      setAnswering(true)
      try {
        const res = await patchRequirement(activeSessionId, next)
        dispatch({ type: 'requirement/received', requirement: res.requirement })
      } catch (err) {
        toast.error(`保存假设失败：${String(err).slice(0, 140)}`)
      } finally {
        setAnswering(false)
      }
      return
    }

    const next: RC = {
      ...requirement,
      missing_fields: requirement.missing_fields.map((field) =>
        field.key === key ? { ...field, selected_value: value } : field,
      ),
    }
    setAnswering(true)
    try {
      const res = await patchRequirement(activeSessionId, next)
      dispatch({ type: 'requirement/received', requirement: res.requirement })
    } catch (err) {
      toast.error(`保存选择失败：${String(err).slice(0, 140)}`)
    } finally {
      setAnswering(false)
    }
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
        setStageIndex(1)
        setLiveDetail(undefined)
        const controller = new AbortController()
        armStream(controller)
        await postConfirmStream(
          activeSessionId,
          {
            toast,
            dispatch,
            setConfirming,
            onTrace: handleProgress,
            onReport: (version) => {
              void refreshVersionsAndSelectLatest(activeSessionId)
              toast.success(`报告 v${version ?? ''} 已生成并保留在当前会话`)
            },
            // Review-2 / spec05：FAILED 版本落库后仅发 error 事件——刷新并选中
            // 最新版本，ReportPaper error band 才能渲染（纯拒绝错误无副作用）
            onFailed: () => {
              void refreshVersionsAndSelectLatest(activeSessionId)
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
    setStageIndex(1)
    setLiveDetail(undefined)
    const controller = new AbortController()
    armStream(controller)
    try {
      await postConfirmStream(
        activeSessionId,
        {
          toast,
          dispatch,
          setConfirming,
          onTrace: handleProgress,
          onReport: (version) => {
            void refreshVersionsAndSelectLatest(activeSessionId)
            toast.success(`报告 v${version ?? ''} 已生成并保留在当前会话`)
          },
          onFailed: () => {
            void refreshVersionsAndSelectLatest(activeSessionId)
          },
        },
        'retry',
        controller.signal,
      )
    } finally {
      setConfirming(false)
    }
  }

  // ── 当轮助手消息的内容（同一时刻只出一种 body）──────────────────────────────
  // 2026-11-05（修订 A4）：结构化 body 与状态文案互斥——否则一轮里会同时出现
  // 「进度条」和「再说一句『正在查询数据』」，正是用户嫌的重复。
  const executing = phase === 'generating' || phase === 'adjusting'
  const retryable = canRetryFailedAction({ phase, error })
  const liveBody = questionField ? (
    <QuestionBar
      bare
      field={questionField}
      onPick={(key, value) => {
        void handleAnswerQuestion(key, value)
      }}
      onSupplement={() => composerRef.current?.focus()}
      disabled={answering}
    />
  ) : showConfirmBar && requirement ? (
    <ConfirmBar
      bare
      card={requirement}
      busy={patching || confirming}
      onConfirm={async () => {
        await handlePatchAndConfirm(requirement)
      }}
      onSupplement={() => composerRef.current?.focus()}
    />
  ) : phase === 'parsing' ? (
    <ParsingCard bare onStop={handleStopParsing} />
  ) : executing ? (
    <ProgressCard
      bare
      adjusting={phase === 'adjusting'}
      stageIndex={stageIndex}
      liveDetail={liveDetail}
      onStop={handleStop}
    />
  ) : phase === 'error' && retryable ? (
    <ErrorCard
      bare
      message={error?.message}
      kind={error?.kind}
      sql={error?.sql}
      onRetry={handleRetry}
      retrying={confirming}
    />
  ) : null

  const liveMarkdown = liveBody
    ? ''
    : phase === 'error'
      ? `${agentCopy('error')}${error?.message ? `\n\n${error.message}` : ''}`
      : phase === 'report_ready'
        ? agentCopy('report_ready')
        : sending
          ? '正在理解你的问题…'
          : (currentTurn?.answer ?? '') || agentCopy(phase)

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
          {/* 历史轮次：一段连续对话（问 + 答 + 过程 + 版本条），永不整表覆盖 */}
          {activeSessionId && historyTurns.length > 0 && (
            <ConversationTimeline
              sessionId={activeSessionId}
              turns={historyTurns}
              // 2026-11-05：历史默认**收起**报告（正文与过程仍显示）。此前「最近一轮保持展开」
              // 是怕上一份报告消失，但那会和新版本一起渲染 → 一屏两张报告。
              expandLatest={false}
            />
          )}

          {/* 当轮：用户消息 + **一条**助手消息。2026-11-05（修订 A4）之前，一轮里最多
              同时有「思考气泡 + 状态气泡 + 进度卡 + 只读大卡」，用户看到的是「生成了一份
              还是两份」。现在过程、状态、动作、进度、错误全部收进同一个气泡。 */}
          {currentTurn && (
            <>
              <UserBubble text={currentTurn.question} />
              <AssistantTurn
                trace={
                  hasTrace ? (
                    <TracePanel
                      entries={timeline}
                      note={timelineNote}
                      running={busy || sending || confirming}
                      // 2026-11-05（plan reasoning-chain-display D3）：当轮默认展开——
                      // 刚问完就能读到「它在想什么」；历史轮次由时间线收起。
                      autoOpen
                    />
                  ) : null
                }
                markdown={liveMarkdown}
              >
                {liveBody}
              </AssistantTurn>
            </>
          )}

          {activeSessionId && selectedReportVersion != null &&
            (turnBaseVersion === null || selectedReportVersion !== turnBaseVersion) && (
            <ReportPaper
              key={`${activeSessionId}-${selectedReportVersion}`}
              sessionId={activeSessionId}
              version={selectedReportVersion}
              requirement={requirement}
              onAdjust={(text) => handleSend(text)}
              onFocusComposer={() => composerRef.current?.focus()}
            />
          )}

          {messages.length === 0 && !requirement && reportVersions.length === 0 && phase === 'idle' && (
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
            // 2026-11-05（用户：「右边那些东西也是点不动的」）：原来只把文字塞进底部输入框，
            // 视觉上什么都没发生。推荐项本身就是「继续分析」的动作——直接发出去。
            if (busy || sending || patching || confirming) {
              toast.error('当前任务还没结束，稍等一下再继续')
              return
            }
            toast.success(`继续分析：${text}`)
            handleSend(text)
          }}
        />
      </div>
    </div>
  )
}

