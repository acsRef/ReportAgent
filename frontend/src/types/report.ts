export type ReportBlockType =
  | 'kpi'
  | 'table'
  | 'chart'
  | 'insight'
  | 'markdown'
  | 'summary'
  | 'alert'
  | 'recommendation'
  | 'section'
  | 'grid'
  | 'report-header'

export interface ReportBlock {
  id: string
  type: ReportBlockType
  title?: string
  data: Record<string, unknown>
}

export interface ReportResponse {
  answer: {
    text?: string
    table?: {
      columns: Array<{ key: string; title: string }>
      rows: Record<string, unknown>[]
    } | null
    chart?: Record<string, unknown> | null
    insight?: string | null
  }
  trace?: unknown[]
}

export type SSEEventType = 'trace' | 'token' | 'report' | 'error' | 'done' | 'clarify'

export interface SSEEvent {
  event: SSEEventType
  data: string
}

export interface AgentStep {
  name: string
  status: 'running' | 'success' | 'error'
  detail?: string
}

export interface TimelineEntry {
  id: string
  nodeName: string
  status: 'pending' | 'running' | 'success' | 'error'
  duration?: string
  timestamp: number
}

export interface TemplateParams {
  year: string
  region: string
}

export interface ReportEntry {
  id: string
  query: string
  content: string
  blocks: ReportBlock[]
  steps: AgentStep[]
  timestamp: number
  status: 'generating' | 'done' | 'error'
}

export interface ReportTemplate {
  id: string
  name: string
  description: string
  params: TemplateParams
  blocks: ReportBlock[]
  createdAt: number
  updatedAt: number
}

export interface ConversationMessage {
  id: number
  role: 'user' | 'assistant'
  content: string | null
  message_type: string
  metadata: unknown
  created_at: string
}
