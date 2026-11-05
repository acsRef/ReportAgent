/**
 * 历史轮次切分与版本归属（plan 2026-11-04-history-turn-pairing）。
 *
 * 背景：此前 `WorkbenchPage` 里这段逻辑内联在 `useMemo` 中，有两个坑——
 *   ① 当轮判定只靠 `lastQuestion` 的字符串相等。会话从左侧列表点回来时
 *      `lastQuestion` 会有一小段是 `null`，那一轮就被当成「历史轮次」渲染，
 *      于是出现一条没有报告的边框小条压在当轮助手消息上方（用户截图即此）。
 *   ② 版本与轮次按下标硬配对（vN ↔ 第 N 轮）。只要中间有一轮没产出报告
 *      （改了需求但没确认），后面所有轮次整体错位。
 *
 * 这里把两件事都收成纯函数，便于穷举边界；组件只负责渲染。
 */
import type { ConversationMessage, ReportVersion } from '../../types/analysis'
import type { TimelineTurn } from './ConversationTimeline'

/** 一轮的提问（时间戳已归一成毫秒，解析失败为 null）。 */
interface TurnQuestion {
  key: string
  text: string
  at: number | null
}

/**
 * 当前轮的问题文本。
 *
 * - `lastQuestion` 有值：它就是用户刚发的那句（发送时立刻写入，最权威）；
 * - 为空且不在 `idle`：**退回最后一个用户提问**——会话快照恢复/切回会话时
 *   `lastQuestion` 还没回填，但屏幕上要展示的正是那个提问；不兜底的话这一轮
 *   会以「无版本历史轮次」出现在时间线里，并留下一条空的「未生成报告」。
 */
export function liveQuestionOf(
  messages: ConversationMessage[],
  lastQuestion: string | null,
  phase: string,
): string | null {
  const explicit = (lastQuestion ?? '').trim()
  if (explicit) return explicit
  if (phase === 'idle') return null
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.role !== 'user') continue
    const text = (msg.content ?? '').trim()
    if (text) return text
  }
  return null
}

/** 解析后端 `isoformat()`（可能不带时区）——两侧同源，比较才可靠。 */
function parseAt(iso: string | undefined | null): number | null {
  if (!iso) return null
  const at = Date.parse(iso)
  return Number.isFinite(at) ? at : null
}

function toQuestions(messages: ConversationMessage[]): TurnQuestion[] {
  const questions: TurnQuestion[] = []
  for (const msg of messages) {
    if (msg.role !== 'user') continue
    const text = (msg.content ?? '').trim()
    if (!text) continue
    questions.push({ key: `turn-${msg.id}`, text, at: parseAt(msg.created_at) })
  }
  return questions
}

/**
 * 版本归属：每个版本归给「最后一个创建时间早于它」的提问。
 *
 * 时间不可用（缺字段/格式意外）时退回下标配对（vN ↔ 第 N 轮），保证老数据也能显示。
 * 同一个版本号只会落在唯一一轮；两版同轮时保留较新的那版。
 */
function pairVersions(
  questions: TurnQuestion[],
  versions: ReportVersion[],
): Map<string, ReportVersion> {
  const byKey = new Map<string, ReportVersion>()
  if (questions.length === 0 || versions.length === 0) return byKey

  const ordered = [...versions].sort((a, b) => a.version - b.version)
  for (const version of ordered) {
    const versionAt = parseAt(version.created_at)
    let index = -1
    if (versionAt !== null) {
      for (let i = questions.length - 1; i >= 0; i--) {
        const at = questions[i].at
        if (at !== null && at <= versionAt) {
          index = i
          break
        }
      }
    }
    if (index < 0) {
      index = Math.min(version.version - 1, questions.length - 1)
    }
    if (index < 0) continue
    byKey.set(questions[index].key, version)
  }
  return byKey
}

/**
 * 历史轮次 = 除当轮以外的所有提问，按出现顺序配对「助手回复」与「报告版本」。
 *
 * 2026-11-05（plan conversation-transcript-messages）：一轮对话 = 问 + 答。
 * 之前只配对版本，历史里没有助手说的话 → 用户发下一句时，上一轮 Agent 的回复
 * 看起来凭空消失。现在把该轮之后的第一条 assistant 文本一起配进去。
 *
 * 版本归属在**全量**提问上算（含当轮），否则当轮拥有的版本会被错误地算到上一轮头上；
 * 算完再把尾部当轮剔除。只有尾部提问会被当成当轮：用户重复问了同一句话时，
 * 更早的那一轮仍要留在历史里（否则上一轮报告凭空消失）。
 */
export function buildHistoryTurns(
  messages: ConversationMessage[],
  versions: ReportVersion[],
  liveQuestion: string | null,
): TimelineTurn[] {
  const questions = toQuestions(messages)
  if (questions.length === 0) return []

  const assistantByKey = pairAssistantText(messages, questions)
  const byKey = pairVersions(questions, versions)
  const turns: TimelineTurn[] = questions.map((question) => ({
    key: question.key,
    question: question.text,
    version: byKey.get(question.key) ?? null,
    answer: assistantByKey.get(question.key) ?? null,
  }))

  const live = (liveQuestion ?? '').trim()
  if (live && turns[turns.length - 1].question === live) turns.pop()
  return turns
}

/** assistant 文本按时间归给「它之前最近的那个提问」。
 *
 * 优先取 `assistant_text`（需求轮那句「我理解为…」）；没有才用最后一条
 * （例如只有 `报告 vN 已生成：…`）。报告本身由版本条承载，不重复进气泡。
 */
function pairAssistantText(
  messages: ConversationMessage[],
  questions: TurnQuestion[],
): Map<string, string> {
  const byKey = new Map<string, string>()
  const primary = new Set<string>()
  let current = -1
  for (const message of messages) {
    if (message.role === 'user') {
      const text = (message.content ?? '').trim()
      if (!text) continue
      current = questions.findIndex((q) => q.key === `turn-${message.id}`)
      continue
    }
    if (message.role !== 'assistant' || current < 0) continue
    const text = (message.content ?? '').trim()
    if (!text) continue
    const key = questions[current].key
    const isPrimary = (message.message_type ?? '') === 'assistant_text'
    if (isPrimary) {
      byKey.set(key, text)
      primary.add(key)
    } else if (!primary.has(key)) {
      byKey.set(key, text)
    }
  }
  return byKey
}
