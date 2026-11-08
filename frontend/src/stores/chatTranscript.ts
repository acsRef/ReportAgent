/**
 * 对话账本（chat transcript）——append-only 消息模型（2026-11-05，plan
 * `2026-11-05-conversation-transcript-messages.md` 修订 A）。
 *
 * 为什么要有这一层：
 * - 此前 store 里的 `messages` 只是「服务端历史快照」，整表替换；当前这一轮
 *   是页面临时拼出来的（`lastQuestion` + `phase` + 全局 `timeline`）。于是
 *   「发下一句 → 上一轮助手说的话消失」在渲染层无法自证，只能靠不停刷新兜。
 * - 现在一条消息就是一个对象：**发送即 append**（用户消息 + 空的助手占位），
 *   SSE 增量更新**最后一条**，`done` 标记完成；服务端历史按 `serverId` 合并，
 *   本地正在流的占位永远不会被整表覆盖掉。
 *
 * 纯函数模块：reducer 只做 `{ ...state, messages: f(state.messages, ...) }`，
 * 所有边界（认领、去重、保尾）都能离线穷举。id 由调用方生成，保持纯函数。
 */
import type { ConversationMessage } from '../types/analysis'
import type { TimelineEntry } from '../types/report'

export type ChatRole = 'user' | 'assistant'

/** `app.conversations.message_type`：落库的思考链路的行类型（2026-11-05）。 */
export const TURN_TRACE_MESSAGE_TYPE = 'turn_trace'

/** 一条消息的生命周期：流式中 → 完成 / 失败。 */
export type ChatStatus = 'streaming' | 'done' | 'error'

export interface ChatMessage {
  /** 本地稳定 id：React key 用它，合并服务端历史时不变（不重挂节点）。 */
  id: string
  /** `app.conversations.id`；落地后回填，是合并去重的唯一依据。 */
  serverId: number | null
  role: ChatRole
  content: string
  createdAt: string
  status: ChatStatus
  /** 该轮产出的报告版本（仅 assistant；没有报告为 null）。 */
  version: number | null
  /** 过程步骤——**属于这条消息**，「过程融进同一条回复」由数据结构保证。 */
  trace: TimelineEntry[]
  /** 过程说明（中断 / 停止 / 重连 / 后台完成）。 */
  traceNote: string | null
}

/** 一次发送 = 一批消息（用户那句 + 助手占位），由 reducer 原子追加。 */
export interface SentTurn {
  userId: string
  assistantId: string
  text: string
  createdAt: string
}

export function newUserMessage(turn: Pick<SentTurn, 'userId' | 'text' | 'createdAt'>): ChatMessage {
  return {
    id: turn.userId,
    serverId: null,
    role: 'user',
    content: turn.text,
    createdAt: turn.createdAt,
    status: 'done',
    version: null,
    trace: [],
    traceNote: null,
  }
}

export function newAssistantMessage(
  turn: Pick<SentTurn, 'assistantId' | 'createdAt'>,
): ChatMessage {
  return {
    id: turn.assistantId,
    serverId: null,
    role: 'assistant',
    content: '',
    createdAt: turn.createdAt,
    status: 'streaming',
    version: null,
    trace: [],
    traceNote: null,
  }
}

/** 发送：用户消息 + 空的助手占位，一次追加（不能只追加用户那条）。 */
export function appendTurn(messages: ChatMessage[], turn: SentTurn): ChatMessage[] {
  return [
    ...messages,
    newUserMessage(turn),
    newAssistantMessage(turn),
  ]
}

/** 最后一条 assistant 消息的下标（-1 = 还没有）。 */
export function liveAssistantIndex(messages: ChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant') return i
  }
  return -1
}

export function liveAssistant(messages: ChatMessage[]): ChatMessage | null {
  const index = liveAssistantIndex(messages)
  return index >= 0 ? messages[index] : null
}

/** 当前轮的过程步骤（页面 `TracePanel` 的数据源）。 */
export function liveTrace(messages: ChatMessage[]): TimelineEntry[] {
  return liveAssistant(messages)?.trace ?? []
}

export function liveTraceNote(messages: ChatMessage[]): string | null {
  return liveAssistant(messages)?.traceNote ?? null
}

/** 改写最后一条 assistant 消息；没有就原样返回（历史只读）。 */
function patchLiveAssistant(
  messages: ChatMessage[],
  patch: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const index = liveAssistantIndex(messages)
  if (index < 0) return messages
  const next = [...messages]
  next[index] = patch(messages[index])
  return next
}

export function withAssistantText(messages: ChatMessage[], text: string): ChatMessage[] {
  return patchLiveAssistant(messages, (message) => ({ ...message, content: text }))
}

export function withAssistantStatus(messages: ChatMessage[], status: ChatStatus): ChatMessage[] {
  return patchLiveAssistant(messages, (message) => ({ ...message, status }))
}

export function withAssistantVersion(messages: ChatMessage[], version: number): ChatMessage[] {
  return patchLiveAssistant(messages, (message) => ({ ...message, version }))
}

/**
 * 收一条过程步骤（trace 帧）。
 *
 * 按 `nodeName` 去重：progress 帧是「同一节点 running → success」两帧、每帧 id 都不同，
 * 按 id 去重会让步骤列表越叠越长。收到新步骤同时清掉上一次的异常说明。
 */
export function withTraceEntry(messages: ChatMessage[], entry: TimelineEntry): ChatMessage[] {
  return patchLiveAssistant(messages, (message) => {
    const index = message.trace.findIndex((item) => item.nodeName === entry.nodeName)
    const trace = [...message.trace]
    if (index >= 0) {
      // 2026-11-05（plan streaming-reasoning）：同一节点的后续帧（含流式续帧）**保留最早的
      // 时间戳**——否则流式每 350ms 换一次时间戳，「已等待 Ns」永远显示 0s，整轮耗时也被压平。
      trace[index] = {
        ...entry,
        timestamp: Math.min(message.trace[index].timestamp, entry.timestamp),
      }
    } else {
      trace.push(entry)
    }
    return { ...message, trace, traceNote: null }
  })
}

export function withTraceNote(messages: ChatMessage[], note: string | null): ChatMessage[] {
  return patchLiveAssistant(messages, (message) => ({ ...message, traceNote: note }))
}

/** 过程收尾原因：done=正常收尾 / stopped=用户停止 / interrupted=断流 / error=后端报错。 */
export type TimelineCloseReason = 'done' | 'stopped' | 'interrupted' | 'error'

export const CLOSE_NOTE: Record<TimelineCloseReason, string | null> = {
  done: null,
  stopped: '已停止显示，任务在后台继续执行，完成后会自动更新',
  interrupted: '连接中断，任务可能在后台继续；恢复后会自动更新',
  error: '执行在中途失败，已完成的步骤保留在上方',
}

/**
 * 一轮结束：把还挂在「进行中」的步骤按原因定性，并同步**消息自身**的状态。
 *
 *   done → success（最后一步只是没来得及发 success 帧）+ 消息 done
 *   stopped → pending（用户停止观看，后台还在跑）+ 消息保持 streaming（轮询会补终态）
 *   interrupted / error → error（真的断在这里）+ 消息 error
 *
 * 2026-11-08（plan interruption-resilience）：消息 status 必须跟着收尾原因走——
 * 否则断流后那条助手消息永远停在 streaming，`ChatTurn.status` 一辈子是「进行中」。
 */
export function closeTrace(
  messages: ChatMessage[],
  reason: TimelineCloseReason,
): ChatMessage[] {
  const stepStatus: TimelineEntry['status'] =
    reason === 'done' ? 'success' : reason === 'stopped' ? 'pending' : 'error'
  const messageStatus: ChatStatus | null =
    reason === 'stopped' ? null : reason === 'done' ? 'done' : 'error'
  const index = liveAssistantIndex(messages)
  if (index < 0) return messages
  const message = messages[index]
  const trace = message.trace.map((entry) =>
    entry.status === 'running' ? { ...entry, status: stepStatus } : entry,
  )
  const note =
    reason === 'done' && trace.every((entry) => entry.status !== 'running')
      ? null
      : CLOSE_NOTE[reason]
  const next = [...messages]
  next[index] = {
    ...message,
    trace,
    traceNote: note,
    ...(messageStatus ? { status: messageStatus } : {}),
  }
  return next
}

// ── 服务端历史合并（按 id 去重，绝不整表覆盖）────────────────────────────────

function normalize(text: string | null | undefined): string {
  return (text ?? '').replace(/\s+/g, ' ').trim()
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * 服务端行是否进对话账本。
 *
 * - `role='system'`（`requirement_patch` 指针行）没有可读文本，不进；
 * - `message_type='report_version'` 的指针行由「版本条」表达，重复渲染会让一轮
 *   冒出两条助手气泡；
 * - `message_type='turn_trace'`（2026-11-05 落库的思考链路）只提供 `trace`
 *   数据，由 `mergeServerHistory` 挂到该轮助手消息上，本身不是消息。
 */
function isTranscriptRow(row: ConversationMessage): boolean {
  if (row.role !== 'user' && row.role !== 'assistant') return false
  const type = row.message_type ?? ''
  if (type === 'report_version' || type === TURN_TRACE_MESSAGE_TYPE) return false
  return normalize(row.content).length > 0 || row.role === 'user'
}

function isTurnTraceRow(row: ConversationMessage): boolean {
  return (row.message_type ?? '') === TURN_TRACE_MESSAGE_TYPE
}

/** `app.conversations.metadata` —— asyncpg 没有 jsonb codec，线上是 JSON 字符串。 */
function rowMetadata(row: ConversationMessage): Record<string, unknown> | null {
  const raw = row.metadata
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'string') {
    try {
      const parsed: unknown = JSON.parse(raw)
      return typeof parsed === 'object' && parsed !== null
        ? (parsed as Record<string, unknown>)
        : null
    } catch {
      return null
    }
  }
  return typeof raw === 'object' ? (raw as Record<string, unknown>) : null
}

/**
 * 落库的思考链路 → `TimelineEntry[]`。
 *
 * 字段是后端 `trace_store.build_trace_metadata` 写下的：
 * `{nodeName, status, detail?, kind?, reasoning?, ts}`——与前端类型一一对应，
 * 不需要第二套映射。缺字段的条目直接丢掉（宁可少一条，不要脏数据）。
 */
export function traceFromServerRow(row: ConversationMessage): TimelineEntry[] {
  const metadata = rowMetadata(row)
  const rawEntries = metadata?.entries
  if (!Array.isArray(rawEntries)) return []
  const entries: TimelineEntry[] = []
  for (const raw of rawEntries) {
    if (typeof raw !== 'object' || raw === null) continue
    const item = raw as Record<string, unknown>
    const nodeName = typeof item.nodeName === 'string' ? item.nodeName : ''
    const status = item.status
    if (!nodeName) continue
    if (status !== 'running' && status !== 'success' && status !== 'error' && status !== 'pending') {
      continue
    }
    const ts = typeof item.ts === 'number' && Number.isFinite(item.ts) ? item.ts : 0
    entries.push({
      id: `srv-${row.id}-${nodeName}-${entries.length}`,
      nodeName,
      status,
      ...(typeof item.detail === 'string' && item.detail ? { detail: item.detail } : {}),
      ...(typeof item.kind === 'string' && item.kind
        ? { kind: item.kind as TimelineEntry['kind'] }
        : {}),
      ...(typeof item.reasoning === 'string' && item.reasoning
        ? { reasoning: item.reasoning }
        : {}),
      timestamp: ts,
    })
  }
  return entries
}

/**
 * 合并过程步骤：**已有顺序不动**，只追加新节点。
 *
 * - 本地实时帧比落库的更新（当轮），同一节点保留本地那条；
 * - 落库的两行（需求阶段 / 执行阶段）按到达顺序追加，最终链路顺序正确；
 * - 重复合并幂等（同一 nodeName 不会出现两次）。
 */
function mergeTraceEntries(
  current: TimelineEntry[],
  incoming: TimelineEntry[],
): TimelineEntry[] {
  if (current.length === 0) return incoming
  const merged = [...current]
  for (const entry of incoming) {
    if (merged.some((item) => item.nodeName === entry.nodeName)) continue
    merged.push(entry)
  }
  return merged
}

function fromServerRow(row: ConversationMessage, index: number): ChatMessage | null {
  const serverId = numberOrNull(row.id)
  const content = row.content ?? ''
  if (!normalize(content) && row.role !== 'user') return null
  return {
    // 服务端行没有本地 id：用 `srv-<id>` 作稳定 key（同一行多次合并得到同一个 key）。
    id: serverId !== null ? `srv-${serverId}` : `srv-${row.created_at ?? ''}-${index}`,
    serverId,
    role: row.role as ChatRole,
    content,
    createdAt: row.created_at ?? '',
    status: 'done',
    version: null,
    trace: [],
    traceNote: null,
  }
}

/**
 * 认领：本地一条还没落地的消息 ↔ 服务端某一新行。
 *
 * 优先按「同角色 + 同内容」精确匹配（用户提问按原话认领）；内容对不上时，
 * 允许**空的助手占位**被认领——服务端 `assistant_text` 到达后把句子回填到同一条气泡，
 * 而不是凭空多出一条助手消息。
 */
function findClaimableRow(
  rows: ConversationMessage[],
  consumed: ReadonlySet<number>,
  message: ChatMessage,
): number {
  const content = normalize(message.content)
  for (let i = 0; i < rows.length; i++) {
    if (consumed.has(i)) continue
    const row = rows[i]
    if ((row.role as ChatRole) !== message.role) continue
    const theirs = normalize(row.content)
    if (content && theirs && content === theirs) return i
  }
  if (message.role !== 'assistant' || content) return -1
  for (let i = 0; i < rows.length; i++) {
    if (consumed.has(i)) continue
    if ((rows[i].role as ChatRole) === 'assistant') return i
  }
  return -1
}

function parseAt(iso: string | null | undefined): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  return Number.isFinite(at) ? at : null
}

/** 按时间把「本地没有对应行」的服务端消息插回它该在的位置（不能一律追加到末尾）。 */
function insertByTime(result: ChatMessage[], message: ChatMessage): void {
  const at = parseAt(message.createdAt)
  if (at === null) {
    result.push(message)
    return
  }
  for (let i = result.length - 1; i >= 0; i--) {
    const mine = parseAt(result[i].createdAt)
    if (mine === null || mine <= at) {
      result.splice(i + 1, 0, message)
      return
    }
  }
  result.unshift(message)
}

/**
 * 合并服务端历史（plan 修订 A3）。
 *
 * **以本地账本为主序**遍历（绝不重排已有消息）：
 * 1. 已落地（有 `serverId`）→ 用服务端内容刷新，位置不动；服务端这次没返回也保留；
 * 2. 未落地 → 认领一行（同内容 / 空助手占位），认领后位置仍不动；
 * 3. 服务端剩下的新行（别的轮次落库、或多出来的行）按时间插入。
 *
 * 这条规则是「历史永久连续、绝不整表覆盖」的实现点：本地正在流的那条、闲聊回复
 * （服务端不落库）都留在原位——早前一版把未认领的本地消息统一追加到末尾，
 * 结果两条闲聊轮的助手消息被挪到后面、错挂到下一轮头上（实测踩到）。
 */
export function mergeServerHistory(
  messages: ChatMessage[],
  server: ConversationMessage[] | null | undefined,
): ChatMessage[] {
  const allRows = server ?? []
  const rows = allRows.filter(isTranscriptRow)
  const consumed = new Set<number>()
  const result: ChatMessage[] = []

  const rowIndexByServerId = new Map<number, number>()
  rows.forEach((row, index) => {
    const serverId = numberOrNull(row.id)
    if (serverId !== null) rowIndexByServerId.set(serverId, index)
  })

  for (const message of messages) {
    const serverId = message.serverId
    if (serverId !== null) {
      const rowIndex = rowIndexByServerId.get(serverId)
      if (rowIndex === undefined) {
        // 服务端这次没返回这一行（分页/清理）——保留，不丢
        result.push(message)
        continue
      }
      consumed.add(rowIndex)
      const row = rows[rowIndex]
      result.push({
        ...message,
        content: normalize(row.content) ? (row.content as string) : message.content,
        createdAt: row.created_at ?? message.createdAt,
      })
      continue
    }

    const claim = findClaimableRow(rows, consumed, message)
    if (claim < 0) {
      result.push(message)
      continue
    }
    consumed.add(claim)
    const row = rows[claim]
    result.push({
      ...message,
      serverId: numberOrNull(row.id) ?? message.serverId,
      content: normalize(row.content) ? (row.content as string) : message.content,
      createdAt: row.created_at ?? message.createdAt,
    })
  }

  rows.forEach((row, index) => {
    if (consumed.has(index)) return
    const created = fromServerRow(row, index)
    if (created) insertByTime(result, created)
  })

  attachPersistedTraces(result, allRows)
  return result
}

/**
 * 把落库的思考链路（`turn_trace` 行）挂到**它在数据库里紧邻的那条助手消息**上。
 *
 * 判定不看时间戳（同一事务里多行 `created_at` 相同，时间比较会串轮）：
 * 沿服务端行序往前找最近一条「已进入结果集的 assistant 行」，用它的 `serverId`
 * 定位结果下标。服务端还没有这一轮的助手消息（正在流）时，退回本地未落地的最后一条。
 *
 * 同一轮会落两行（需求阶段一行、执行阶段一行），按到达顺序合并；
 * 本地实时 trace 优先（比库里新），重复合并幂等（按 nodeName 去重）。
 */
function attachPersistedTraces(result: ChatMessage[], rows: ConversationMessage[]): void {
  const indexByRowId = new Map<number, number>()
  result.forEach((message, index) => {
    if (message.serverId !== null) indexByRowId.set(message.serverId, index)
  })

  for (let r = 0; r < rows.length; r++) {
    const row = rows[r]
    if (!isTurnTraceRow(row)) continue
    const persisted = traceFromServerRow(row)
    if (persisted.length === 0) continue

    let target = -1
    for (let k = r - 1; k >= 0; k--) {
      const candidate = rows[k]
      if (candidate.role !== 'assistant' || !isTranscriptRow(candidate)) continue
      const id = numberOrNull(candidate.id)
      const index = id === null ? undefined : indexByRowId.get(id)
      if (index !== undefined) {
        target = index
        break
      }
    }
    if (target < 0) {
      for (let i = result.length - 1; i >= 0; i--) {
        if (result[i].role === 'assistant' && result[i].serverId === null) {
          target = i
          break
        }
      }
    }
    if (target < 0) continue
    result[target] = {
      ...result[target],
      trace: mergeTraceEntries(result[target].trace, persisted),
    }
  }
}
