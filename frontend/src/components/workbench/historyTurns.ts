/**
 * 轮次切分与版本归属（plan 2026-11-04-history-turn-pairing，2026-11-05 修订 A 收敛）。
 *
 * 数据源是对话账本 `ChatMessage[]`（stores/chatTranscript）——**唯一**来源：
 * 一个 user 消息开一轮，其后的 assistant 消息并入该轮（答 + 过程 + 版本）。
 * 当轮与历史切分也只按账本判定（`currentTurnKey`），不再有 `lastQuestion`
 * 这种「页面自己记一句」的第二份状态。
 *
 * 版本归属按时间算而不是按下标：中间有一轮没产出报告时（改了需求但没确认），
 * 按下标硬配对会让后面所有轮次整体错位。
 */
import type { ChatMessage } from '../../stores/chatTranscript'
import type { ReportVersion } from '../../types/analysis'
import type { TimelineEntry } from '../../types/report'

export interface ChatTurn {
  /** 稳定 key（来自该轮 user 消息的本地 id；合并历史时不变）。 */
  key: string
  question: string
  /** 该轮助手回复的文本（一轮 = 问 + 答，缺了它历史就断）。 */
  answer: string | null
  /** 该轮的过程步骤（思考摘要 / 执行状态），与答同属一条消息。 */
  trace: TimelineEntry[]
  traceNote: string | null
  /** 该轮助手消息的生命周期状态（本轮还在流 = streaming）。 */
  status: ChatMessage['status']
  /** 该轮产出的报告版本（chitchat / 失败轮可能没有）。 */
  version: ReportVersion | null
  /** 提问时间（毫秒；解析失败为 null）。 */
  at: number | null
}

/** 解析后端 `isoformat()`（可能不带时区）——两侧同源，比较才可靠。 */
function parseAt(iso: string | undefined | null): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  return Number.isFinite(at) ? at : null
}

function normalize(text: string | null | undefined): string {
  return (text ?? '').trim()
}

/** 同一轮里多条 assistant 消息（目前只会有一条）合并成「一个答」。 */
function mergeTrace(into: TimelineEntry[], extra: TimelineEntry[]): TimelineEntry[] {
  const merged = [...into]
  for (const entry of extra) {
    const index = merged.findIndex((item) => item.nodeName === entry.nodeName)
    if (index >= 0) merged[index] = entry
    else merged.push(entry)
  }
  return merged
}

/**
 * 对话账本 → 轮次列表（含当轮）。
 *
 * 孤立的 assistant 消息（前面没有 user）不进轮次：服务端会话总是以提问开始，
 * 这种行只可能是指针残留。
 */
export function buildChatTurns(
  messages: ChatMessage[],
  versions: ReportVersion[],
): ChatTurn[] {
  const turns: ChatTurn[] = []

  for (const message of messages) {
    const text = normalize(message.content)
    if (message.role === 'user') {
      if (!text) continue
      turns.push({
        key: `turn-${message.id}`,
        question: text,
        answer: null,
        trace: [],
        traceNote: null,
        status: message.status,
        version: null,
        at: parseAt(message.createdAt),
      })
      continue
    }

    const turn = turns[turns.length - 1]
    if (!turn) continue
    if (text) turn.answer = turn.answer ? `${turn.answer}\n\n${text}` : text
    turn.trace = mergeTrace(turn.trace, message.trace)
    if (message.traceNote) turn.traceNote = message.traceNote
    turn.status = message.status
  }

  applyVersions(turns, messages, versions)
  return turns
}

/**
 * 版本归属：先按时间（每个版本归给「最后一个创建时间早于它」的提问），
 * 时间不可用时退回下标配对（vN ↔ 第 N 轮），保证老数据也能显示。
 * 该轮助手消息自己带了版本号（SSE `report` 帧）时以它为准——实时轮次
 * 不需要等时间对齐，也免受客户端时钟偏差影响。
 */
function applyVersions(
  turns: ChatTurn[],
  messages: ChatMessage[],
  versions: ReportVersion[],
): void {
  const attached = new Map<string, number>()
  let current = ''
  for (const message of messages) {
    if (message.role === 'user') current = `turn-${message.id}`
    if (message.role === 'assistant' && message.version !== null && current) {
      attached.set(current, message.version)
    }
  }

  if (turns.length === 0 || versions.length === 0) return

  const byVersion = new Map<number, ReportVersion>()
  for (const version of versions) byVersion.set(version.version, version)

  const ordered = [...versions].sort((a, b) => a.version - b.version)
  const paired = new Map<string, ReportVersion>()
  for (const version of ordered) {
    const versionAt = parseAt(version.created_at)
    let index = -1
    if (versionAt !== null) {
      for (let i = turns.length - 1; i >= 0; i--) {
        const at = turns[i].at
        if (at !== null && at <= versionAt) {
          index = i
          break
        }
      }
    }
    if (index < 0) {
      index = Math.min(version.version - 1, turns.length - 1)
    }
    if (index < 0) continue
    paired.set(turns[index].key, version)
  }

  for (const turn of turns) {
    const own = attached.get(turn.key)
    turn.version = own != null ? byVersion.get(own) ?? null : paired.get(turn.key) ?? null
  }
}

/**
 * 当轮 = 最后一个用户提问所在的那一轮（`idle` 时没有当轮，整段都是历史）。
 *
 * 用相位而不是「消息是否还在流」判定：确认/追问阶段助手消息已经落库（status=done），
 * 但这一轮仍然是「正在交谈的那一轮」，提问条/确认条必须留在它里面。
 */
export function currentTurnKey(
  messages: ChatMessage[],
  phase: string,
): string | null {
  if (phase === 'idle') return null
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]
    if (message.role !== 'user') continue
    if (!normalize(message.content)) continue
    return `turn-${message.id}`
  }
  return null
}
