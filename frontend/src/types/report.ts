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

export type ChatCardType = 'options_group' | 'confirm_card' | 'preview_card' | 'intent_card'
export type IntentPhase = 'idle' | 'analyzing' | 'clarifying' | 'running' | 'report' | 'error'
export type IntentTool =
  | 'group_compare'
  | 'trend_analysis'
  | 'detect_anomaly'
  | 'chart_advisor'
  | 'insight_analyst'

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

export type SSEEventType = 'trace' | 'token' | 'report' | 'error' | 'done' | 'clarify' | 'thinking' | 'card'

export interface SSEEvent {
  event: SSEEventType
  data: string
}

export interface AgentStep {
  name: string
  status: 'running' | 'success' | 'error'
  detail?: string
}

/** P11 progress 族（trace 载荷细化）：kind × status 推导 started/completed/failed。 */
export type TraceProgressKind = 'agent' | 'tool' | 'sql' | 'repair' | 'report'

export interface TimelineEntry {
  id: string
  nodeName: string
  status: 'pending' | 'running' | 'success' | 'error'
  detail?: string
  kind?: TraceProgressKind
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
  metadata?: { cards?: ChatCard[] } | null
  created_at: string
}

/** Chat message cards — embedded inside AI bubbles */
export interface OptionTag {
  label: string
  value: string
  selected: boolean
}

export interface OptionsGroup {
  id: string
  version: number
  type: 'options_group'
  label: string
  options: OptionTag[]
  multi?: boolean
  synthesized_query?: string
}

export interface ConfirmItem {
  icon?: string
  text: string
}

export interface ConfirmCard {
  id: string
  version: number
  type: 'confirm_card'
  title: string
  items: ConfirmItem[]
  onConfirmAction: string
  onModifyAction: string
  synthesized_query?: string
}

export interface PreviewKpi {
  label: string
  value: string
  trend?: string
}

export interface PreviewCard {
  id: string
  version: number
  type: 'preview_card'
  title: string
  kpis: PreviewKpi[]
  chartType?: string
  insight: string
}

/**
 * Intent card (stage 1 of the report-generation UX). The LLM picks 3-4
 * candidate analyses based on the user's question and lists them here.
 * The user clicks one to pick an analytical direction.
 */
export interface IntentOption {
  label: string
  description?: string
  tool: IntentTool
  params_preview?: Record<string, unknown>
}

export interface IntentCardPayload {
  title?: string
  options: IntentOption[]
}

export interface IntentCard {
  id: string
  version: number
  type: 'intent_card'
  payload: IntentCardPayload
  chosen_tool?: IntentTool
  confidence?: number
  reasoning?: string
}

export type ChatCard = OptionsGroup | ConfirmCard | PreviewCard | IntentCard
export type ChatCardVersionPatch = { version: number } & ChatCard

export function isChatCard(x: unknown): x is ChatCard {
  return (
    typeof x === 'object' && x !== null &&
    'type' in x && typeof (x as { type: unknown }).type === 'string' &&
    ['options_group', 'preview_card', 'confirm_card', 'intent_card'].includes((x as { type: string }).type)
  )
}
