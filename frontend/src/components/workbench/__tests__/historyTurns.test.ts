import { describe, expect, it } from 'vitest'
import { buildHistoryTurns, liveQuestionOf } from '../historyTurns'
import type { ConversationMessage, ReportVersion } from '../../../types/analysis'

function msg(id: number, role: ConversationMessage['role'], content: string, at: string): ConversationMessage {
  return { id, role, message_type: 'text', content, created_at: at }
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

describe('liveQuestionOf — 当轮问题兜底', () => {
  const messages = [
    msg(1, 'user', '2024年华东销售趋势', '2026-11-04T13:00:00'),
    msg(2, 'system', '', '2026-11-04T13:00:10'),
  ]

  it('lastQuestion 有值时直接用它', () => {
    expect(liveQuestionOf(messages, '  改按月看  ', 'parsing')).toBe('改按月看')
  })

  // 会话从左侧列表点回来时 lastQuestion 会有一小段是 null：此时最后一个提问
  // 就是屏幕上要展示的那轮，必须兜底，否则它会漏进历史时间线。
  it('lastQuestion 为空且非 idle 时退回最后一个用户提问', () => {
    expect(liveQuestionOf(messages, null, 'awaiting_confirm')).toBe('2024年华东销售趋势')
  })

  it('idle 时不做兜底（新分析画布不该冒出上一句提问）', () => {
    expect(liveQuestionOf(messages, null, 'idle')).toBeNull()
  })

  it('没有任何用户消息时返回 null', () => {
    expect(liveQuestionOf([messages[1]], null, 'parsing')).toBeNull()
  })
})

describe('buildHistoryTurns — 当轮剔除与版本归属', () => {
  it('尾部当轮提问被剔除，历史只留更早的轮次', () => {
    const messages = [
      msg(1, 'user', '问题一', '2026-11-04T13:00:00'),
      msg(2, 'user', '问题二', '2026-11-04T13:05:00'),
    ]
    const turns = buildHistoryTurns(messages, [version(1, '2026-11-04T13:01:00')], '问题二')
    expect(turns).toHaveLength(1)
    expect(turns[0].question).toBe('问题一')
    expect(turns[0].version?.version).toBe(1)
  })

  it('没有当轮提问时（lastQuestion 为 null 且 idle）保留全部历史', () => {
    const messages = [msg(1, 'user', '问题一', '2026-11-04T13:00:00')]
    const turns = buildHistoryTurns(messages, [], null)
    expect(turns).toHaveLength(1)
    expect(turns[0].version).toBeNull()
  })

  // 中间轮没有报告时，后面的轮次不该去抢别人的版本（旧实现按下标配对会整体错位）
  it('无报告的轮次不影响后续轮次的版本归属', () => {
    const messages = [
      msg(1, 'user', '问题一', '2026-11-04T13:00:00'),
      msg(2, 'user', '问题二', '2026-11-04T13:10:00'),
      msg(3, 'user', '问题三', '2026-11-04T13:20:00'),
    ]
    const versions = [version(1, '2026-11-04T13:15:00'), version(2, '2026-11-04T13:25:00')]
    const turns = buildHistoryTurns(messages, versions, '问题三')
    expect(turns.map((t) => t.question)).toEqual(['问题一', '问题二'])
    expect(turns[0].version).toBeNull()
    expect(turns[1].version?.version).toBe(1)
  })

  it('时间不可解析时退回下标配对', () => {
    const messages = [
      msg(1, 'user', '问题一', ''),
      msg(2, 'user', '问题二', ''),
    ]
    const turns = buildHistoryTurns(messages, [version(1, ''), version(2, '')], null)
    expect(turns[0].version?.version).toBe(1)
    expect(turns[1].version?.version).toBe(2)
  })

  it('忽略空内容与助手消息', () => {
    const messages = [
      msg(1, 'assistant', '报告已生成', '2026-11-04T13:00:00'),
      msg(2, 'user', '   ', '2026-11-04T13:00:01'),
      msg(3, 'user', '问题一', '2026-11-04T13:00:02'),
      msg(4, 'user', '问题二', '2026-11-04T13:00:03'),
    ]
    const turns = buildHistoryTurns(messages, [], '问题二')
    expect(turns.map((t) => t.question)).toEqual(['问题一'])
  })

  it('用户重复问同一句话时，更早那一轮仍留在历史里', () => {
    const messages = [
      msg(1, 'user', '查销售额', '2026-11-04T13:00:00'),
      msg(2, 'user', '查销售额', '2026-11-04T13:10:00'),
    ]
    const turns = buildHistoryTurns(messages, [version(1, '2026-11-04T13:01:00')], '查销售额')
    expect(turns).toHaveLength(1)
    expect(turns[0].version?.version).toBe(1)
  })
})
