/** 对话账本 → 轮次（2026-11-05 修订 A：唯一数据源是 ChatMessage[]）。 */
import { describe, expect, it } from 'vitest'
import { buildChatTurns, currentTurnKey } from '../historyTurns'
import type { ChatMessage } from '../../../stores/chatTranscript'
import type { ReportVersion } from '../../../types/analysis'

function chat(
  id: string,
  role: ChatMessage['role'],
  content: string,
  at: string,
  extra: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    id,
    serverId: null,
    role,
    content,
    createdAt: at,
    status: 'done',
    version: null,
    trace: [],
    traceNote: null,
    ...extra,
  }
}

function version(version: number, at: string, status: ReportVersion['status'] = 'done'): ReportVersion {
  return {
    id: `r-${version}`,
    session_id: 'sid-1',
    version,
    parent_version: version > 1 ? version - 1 : null,
    title: `报告 ${version}`,
    status,
    report: { answer: {} },
    created_at: at,
  }
}

const user = (id: string, text: string, at: string) => chat(id, 'user', text, at)
const ai = (id: string, text: string, at: string, extra: Partial<ChatMessage> = {}) =>
  chat(id, 'assistant', text, at, extra)

describe('currentTurnKey — 当轮判定', () => {
  const messages = [
    user('u1', '问题一', '2026-11-04T13:00:00Z'),
    ai('a1', '好，我理解为问题一。', '2026-11-04T13:00:10Z'),
  ]

  it('非 idle 时当轮 = 最后一个提问', () => {
    expect(currentTurnKey(messages, 'parsing')).toBe('turn-u1')
    expect(currentTurnKey(messages, 'awaiting_confirm')).toBe('turn-u1')
  })

  it('idle 时没有当轮（整段都是历史）', () => {
    expect(currentTurnKey(messages, 'idle')).toBeNull()
  })

  it('没有用户消息时返回 null', () => {
    expect(currentTurnKey([ai('a1', '你好', '')], 'parsing')).toBeNull()
  })

  it('空内容的用户消息不算提问', () => {
    expect(currentTurnKey([user('u1', '   ', '')], 'parsing')).toBeNull()
  })
})

describe('buildChatTurns — 问 + 答 + 过程 + 版本', () => {
  it('assistant 文本与过程并入同一条回复', () => {
    const messages = [
      user('u1', '2024年各区域销售额', '2026-11-04T13:00:00Z'),
      ai('a1', '好，我理解为：2024年的销售额。', '2026-11-04T13:00:10Z', {
        trace: [{ id: 't1', nodeName: '解析需求', status: 'success', timestamp: 1 }],
      }),
      ai('a2', '报告已生成。', '2026-11-04T13:01:00Z'),
    ]
    const turns = buildChatTurns(messages, [])
    expect(turns).toHaveLength(1)
    expect(turns[0].question).toBe('2024年各区域销售额')
    expect(turns[0].answer).toBe('好，我理解为：2024年的销售额。\n\n报告已生成。')
    expect(turns[0].trace).toHaveLength(1)
  })

  it('空内容与孤立的助手消息不产生轮次', () => {
    const messages = [
      ai('a0', '欢迎', '2026-11-04T12:59:00Z'),
      user('u1', '   ', '2026-11-04T13:00:00Z'),
      user('u2', '问题一', '2026-11-04T13:00:02Z'),
    ]
    const turns = buildChatTurns(messages, [])
    expect(turns.map((turn) => turn.question)).toEqual(['问题一'])
  })

  it('用户重复问同一句话时也各成一轮', () => {
    const messages = [
      user('u1', '查销售额', '2026-11-04T13:00:00Z'),
      user('u2', '查销售额', '2026-11-04T13:10:00Z'),
    ]
    const turns = buildChatTurns(messages, [version(1, '2026-11-04T13:01:00Z')])
    expect(turns).toHaveLength(2)
    expect(turns[0].version?.version).toBe(1)
    expect(turns[1].version).toBeNull()
  })

  it('版本按时间归属：中间无报告的轮次不影响后续轮次', () => {
    const messages = [
      user('u1', '问题一', '2026-11-04T13:00:00Z'),
      user('u2', '问题二', '2026-11-04T13:10:00Z'),
      user('u3', '问题三', '2026-11-04T13:20:00Z'),
    ]
    const versions = [version(1, '2026-11-04T13:15:00Z'), version(2, '2026-11-04T13:25:00Z')]
    const turns = buildChatTurns(messages, versions)
    expect(turns.map((t) => t.question)).toEqual(['问题一', '问题二', '问题三'])
    expect(turns[0].version).toBeNull()
    expect(turns[1].version?.version).toBe(1)
    expect(turns[2].version?.version).toBe(2)
  })

  it('时间不可解析时退回下标配对', () => {
    const messages = [user('u1', '问题一', ''), user('u2', '问题二', '')]
    const turns = buildChatTurns(messages, [version(1, ''), version(2, '')])
    expect(turns[0].version?.version).toBe(1)
    expect(turns[1].version?.version).toBe(2)
  })

  it('助手消息自带版本号时以它为准（SSE report 帧，免受时钟偏差影响）', () => {
    const messages = [
      user('u1', '问题一', '2026-11-04T13:00:00Z'),
      ai('a1', '报告已生成', '2026-11-04T13:01:00Z', { version: 7 }),
    ]
    const turns = buildChatTurns(messages, [
      version(1, '2026-11-04T13:00:30Z'),
      version(7, '2026-11-04T13:01:00Z'),
    ])
    expect(turns[0].version?.version).toBe(7)
  })

  it('没有版本时不画版本（chitchat 轮）', () => {
    const messages = [
      user('u1', '你好', '2026-11-04T13:00:00Z'),
      ai('a1', '你好！有什么可以帮你的？', '2026-11-04T13:00:01Z'),
    ]
    const turns = buildChatTurns(messages, [])
    expect(turns[0].version).toBeNull()
    expect(turns[0].answer).toBe('你好！有什么可以帮你的？')
  })
})
