import { create } from 'zustand'
import { v4 as uuid } from 'uuid'
import type {
  AgentStep,
  ChatCard,
  ConversationMessage,
  IntentPhase,
  ReportBlock,
  ReportEntry,
  ReportTemplate,
  TemplateParams,
  TimelineEntry,
} from '../types/report'
import { isChatCard } from '../types/report'
import { chatStream, parseReportData, parseTraceData } from '../api/chat'
import { fetchSessions as fetchSessionsAPI, fetchConversations as fetchConversationsAPI } from '../api/api'
import { adaptReport } from '../adapter/reportAdapter'

const SESSION_KEY = 'ragent_session_id'
const SESSION_TS_KEY = 'ragent_session_ts'
const SESSION_MAX_AGE_MS = 24 * 60 * 60 * 1000
const HISTORY_KEY = 'ragent_history'
const TEMPLATES_KEY = 'ragent_templates'

let activeAbort: AbortController | null = null

function loadFromStorage<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback } catch { return fallback }
}

function saveToStorage(key: string, data: unknown) {
  try { localStorage.setItem(key, JSON.stringify(data)) } catch { /* quota exceeded */ }
}

function loadSessionId(): string {
  const stored = localStorage.getItem(SESSION_KEY)
  const ts = localStorage.getItem(SESSION_TS_KEY)
  if (stored && ts) {
    const age = Date.now() - Number(ts)
    if (age < SESSION_MAX_AGE_MS) return stored
  }
  const id = uuid()
  localStorage.setItem(SESSION_KEY, id)
  localStorage.setItem(SESSION_TS_KEY, String(Date.now()))
  return id
}

type ViewMode = 'chat' | 'running' | 'report'
type ReportStyle = 'flat'

interface SessionStore {
  sessionId: string
  sessionLabel: string
  currentReport: ReportEntry | null
  reports: ReportEntry[]
  templates: ReportTemplate[]
  busy: boolean
  cardsByMessage: Record<string, ChatCard[]>
  pendingReportBlocks: ReportBlock[] | null
  currentSteps: AgentStep[]
  timeline: TimelineEntry[]
  templateParams: TemplateParams
  error: string | null
  viewMode: ViewMode
  reportStyle: ReportStyle
  sessions: Array<{ session_id: string; msg_count: number; first_message: string; last_message: string }>
  chatMessages: ConversationMessage[]
  intentPhase: IntentPhase
  lastTraceAt: number
  busyStartedAt: number | null

  setBusy: (busy: boolean) => void
  resetSession: () => void
  sendMessage: (text: string, options?: { refineOf?: string; chosenTool?: string }) => Promise<void>
  cancel: () => void
  selectReport: (id: string) => void
  setTemplateParams: (params: Partial<TemplateParams>) => void
  setViewMode: (mode: ViewMode) => void
  setReportStyle: (style: ReportStyle) => void
  saveAsTemplate: (name: string, description: string) => void
  deleteTemplate: (id: string) => void
  fetchSessionsList: () => Promise<void>
  loadConversation: (sessionId: string) => Promise<void>
  setIntentPhase: (phase: IntentPhase) => void
  tick: () => void
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessionId: loadSessionId(),
  sessionLabel: `#${Math.floor(Math.random() * 9000 + 1000)}`,
  currentReport: null,
  reports: loadFromStorage<ReportEntry[]>(HISTORY_KEY, []),
  templates: loadFromStorage<ReportTemplate[]>(TEMPLATES_KEY, []),
  busy: false,
  cardsByMessage: {},
  pendingReportBlocks: null,
  currentSteps: [],
  timeline: [],
  templateParams: { year: '2024', region: '华东区域' },
  error: null,
  viewMode: 'chat',
  reportStyle: 'flat',
  sessions: [],
  chatMessages: [],
  intentPhase: 'idle',
  lastTraceAt: Date.now(),
  busyStartedAt: null,

  setBusy: (busy: boolean) => {
    set({
      busy,
      busyStartedAt: busy ? Date.now() : null,
      lastTraceAt: Date.now(),
    })
  },

  setIntentPhase: (phase: IntentPhase) => set({ intentPhase: phase }),

  tick: () => set({ lastTraceAt: Date.now() }),

  resetSession: () => {
    const id = uuid()
    localStorage.setItem(SESSION_KEY, id)
    set({
      sessionId: id,
      sessionLabel: `#${Math.floor(Math.random() * 9000 + 1000)}`,
      currentReport: null,
      timeline: [],
      error: null,
      chatMessages: [],
      cardsByMessage: {},
      pendingReportBlocks: null,
    })
  },

  selectReport: (id: string) => {
    const report = get().reports.find((r) => r.id === id)
    if (report) set({ currentReport: report })
  },

  setTemplateParams: (params: Partial<TemplateParams>) => {
    set((s) => ({ templateParams: { ...s.templateParams, ...params } }))
  },

  setViewMode: (mode: ViewMode) => set({ viewMode: mode }),

  setReportStyle: (style: ReportStyle) => set({ reportStyle: style }),

  saveAsTemplate: (name: string, description: string) => {
    const { currentReport, templateParams, templates } = get()
    if (!currentReport) return
    const tmpl: ReportTemplate = {
      id: uuid(),
      name,
      description,
      params: { ...templateParams },
      blocks: currentReport.blocks,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    const updated = [...templates, tmpl]
    set({ templates: updated })
    saveToStorage(TEMPLATES_KEY, updated)
  },

  deleteTemplate: (id: string) => {
    const updated = get().templates.filter((t) => t.id !== id)
    set({ templates: updated })
    saveToStorage(TEMPLATES_KEY, updated)
  },

  fetchSessionsList: async () => {
    try {
      const data = await fetchSessionsAPI()
      set({ sessions: data.sessions })
    } catch { /* not authenticated or network error */ }
  },

  loadConversation: async (sessionId: string) => {
    set({ sessionId, currentReport: null, viewMode: 'chat' })
    localStorage.setItem(SESSION_KEY, sessionId)
    try {
      const data = await fetchConversationsAPI(sessionId)
      set({ chatMessages: data.messages as ConversationMessage[] })
    } catch {
      set({ chatMessages: [] })
    }
  },

  sendMessage: async (text: string, options) => {
    if (get().busy) return

    const { sessionId, reports } = get()
    const entry: ReportEntry = {
      id: uuid(),
      query: text,
      content: '',
      blocks: [],
      steps: [],
      timestamp: Date.now(),
      status: 'generating',
    }

    const chosenTool = options?.chosenTool
    void options?.refineOf
    const controller = new AbortController()
    activeAbort = controller

    set({
      currentReport: entry,
      reports: [...reports, entry],
      busy: true,
      busyStartedAt: Date.now(),
      lastTraceAt: Date.now(),
      pendingReportBlocks: null,
      currentSteps: [],
      timeline: [],
      error: null,
      viewMode: 'chat',
      intentPhase: 'analyzing',
    })

    try {
      const traceStartTimes: Record<string, number> = {}

      await chatStream(
        text,
        sessionId,
        (event) => {
          // tick heartbeat so the busy-timeout in the UI knows the stream is alive
          void get().tick()
          set((s) => {
          if (!s.currentReport) return s
          const report = { ...s.currentReport! }

          switch (event.event) {
            case 'thinking': {
              console.debug('[thinking]', event.data)
              break
            }

            case 'card': {
              try {
                const envelope: unknown = JSON.parse(event.data)
                if (typeof envelope !== 'object' || envelope === null) break

                const version = 'version' in envelope && typeof envelope.version === 'number'
                  ? envelope.version
                  : null
                const type = 'type' in envelope && typeof envelope.type === 'string'
                  ? envelope.type
                  : null
                const payload = 'payload' in envelope ? envelope.payload : null
                if (version === null || !type || typeof payload !== 'object' || payload === null) break

                // Server-side envelopes don't carry an id; mint a stable one
                // off (type + payload) so successive versions of the same card
                // upsert instead of duplicating.
                const idHint =
                  'id' in envelope && typeof (envelope as { id?: unknown }).id === 'string'
                    ? (envelope as { id: string }).id
                    : `${type}-${report.id}`

                const candidate: unknown = { ...payload, type, version, id: idHint }
                if (!isChatCard(candidate)) break

                const existing = s.cardsByMessage[report.id] ?? []
                const matchingIndex = existing.findIndex(
                  (card) => card.id === candidate.id && card.version === candidate.version,
                )
                const cards = [...existing]
                if (matchingIndex >= 0) cards[matchingIndex] = candidate as ChatCard
                else cards.push(candidate as ChatCard)

                const nextPhase = type === 'intent_card' || type === 'options_group'
                  ? 'clarifying'
                  : s.intentPhase

                return {
                  currentReport: report,
                  reports: s.reports.map((item) => (item.id === report.id ? report : item)),
                  currentSteps: report.steps,
                  timeline: s.timeline,
                  intentPhase: nextPhase,
                  cardsByMessage: { ...s.cardsByMessage, [report.id]: cards },
                }
              } catch { /* ignore malformed card payload */ }
              break
            }

            case 'token': {
              try {
                const parsed = JSON.parse(event.data)
                report.content += parsed.text || ''
              } catch { /* ignore */ }
              break
            }

            case 'trace': {
              const trace = parseTraceData(event.data)
              if (trace) {
                const cleanName = trace.step
                  .replace(/^(工具调用|节点开始): /, '')
                  .replace(/^(工具完成|节点完成): /, '')
                const isStart = /^(工具调用|节点开始):/.test(trace.step)
                const isEnd = /^(工具完成|节点完成):/.test(trace.step)

                const step: AgentStep = {
                  name: trace.step,
                  status: trace.status === 'success' ? 'success' : 'running',
                  detail: trace.detail,
                }
                const exists = report.steps.findIndex((s) => s.name === trace.step)
                if (exists >= 0) {
                  report.steps[exists] = step
                } else {
                  report.steps = [...report.steps, step]
                }

                let timeline = [...s.timeline]
                if (isStart) {
                  traceStartTimes[cleanName] = Date.now()
                  const existingIdx = timeline.findIndex((t) => t.nodeName === cleanName)
                  const newEntry: TimelineEntry = {
                    id: uuid(),
                    nodeName: cleanName,
                    status: 'running',
                    timestamp: Date.now(),
                  }
                  if (existingIdx >= 0) {
                    timeline[existingIdx] = newEntry
                  } else {
                    timeline = [...timeline, newEntry]
                  }
                } else if (isEnd) {
                  const startTime = traceStartTimes[cleanName]
                  const elapsed = startTime ? Date.now() - startTime : 0
                  const existingIdx = timeline.findIndex((t) => t.nodeName === cleanName)
                  const entry: TimelineEntry = {
                    id: uuid(),
                    nodeName: cleanName,
                    status: 'success',
                    duration: elapsed < 1000 ? `${elapsed}ms` : `${(elapsed / 1000).toFixed(1)}s`,
                    timestamp: Date.now(),
                  }
                  if (existingIdx >= 0) {
                    timeline[existingIdx] = entry
                  } else {
                    timeline = [...timeline, entry]
                  }
                }
                delete traceStartTimes[cleanName]
              }
              break
            }

            case 'report': {
              const resp = parseReportData(event.data)
              if (resp) {
                report.blocks = adaptReport(resp)
                return {
                  currentReport: report,
                  reports: s.reports.map((item) => (item.id === report.id ? report : item)),
                  currentSteps: report.steps,
                  timeline: s.timeline,
                  intentPhase: 'report',
                  pendingReportBlocks: report.blocks,
                }
              }
              break
            }

            case 'error': {
              const msg = (() => {
                try { return String(JSON.parse(event.data) || event.data) }
                catch { return event.data || '未知错误' }
              })()
              return {
                currentReport: report,
                reports: s.reports.map((item) => (item.id === report.id ? report : item)),
                currentSteps: report.steps,
                timeline: s.timeline,
                intentPhase: 'error',
                error: msg,
              }
            }

            case 'clarify': {
              try {
                const data = JSON.parse(event.data)
                const question = data.question || event.data
                report.content = question
                report.blocks = [{
                  id: 'clarify',
                  type: 'insight',
                  title: '需要进一步确认',
                  data: { content: question },
                }]
              } catch {
                report.content = event.data
              }
              return {
                currentReport: report,
                reports: s.reports.map((item) => (item.id === report.id ? report : item)),
                currentSteps: report.steps,
                timeline: s.timeline,
                intentPhase: 'clarifying',
              }
            }
          }

          return {
            currentReport: report,
            reports: s.reports.map((r) => (r.id === report.id ? report : r)),
            currentSteps: report.steps,
            timeline: s.timeline,
            lastTraceAt: Date.now(),
          }
        })
      }, controller.signal, chosenTool)
    } catch (err: unknown) {
      if (!controller.signal.aborted) {
        const errorMsg = err instanceof Error ? err.message : String(err)
        set({ error: errorMsg })
      }
    } finally {
      set((s) => {
        if (s.currentReport) {
          const report = {
            ...s.currentReport,
            status: s.currentReport.status === 'generating' ? 'done' as const : s.currentReport.status,
          }
          const reports = s.reports.map((r) => (r.id === report.id ? report : r))
          saveToStorage(HISTORY_KEY, reports.slice(-50))
          return {
            busy: false,
            busyStartedAt: null,
            intentPhase: s.intentPhase === 'error' ? 'error' : 'idle',
            currentReport: report,
            reports,
            timeline: s.timeline,
            viewMode: 'chat' as ViewMode,
          }
        }
        return { busy: false, busyStartedAt: null }
      })
      if (activeAbort === controller) activeAbort = null
      get().fetchSessionsList()
    }
  },

  cancel: () => {
    activeAbort?.abort()
    activeAbort = null
    set({
      busy: false,
      busyStartedAt: null,
      intentPhase: 'idle',
    })
  },
}))
