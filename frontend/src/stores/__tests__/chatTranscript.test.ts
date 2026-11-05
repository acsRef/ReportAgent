/**
 * 对话账本合并（2026-11-05，plan conversation-transcript-messages 修订 A3）。
 *
 * 这组用例钉的是用户最在意的两件事：
 *   1. 发一条消息后，之前 Agent 的回复**不会消失**（不整表覆盖）；
 *   2. 同一条消息不会因为「本地一条 + 服务端一条」变成两条（按 id / 内容去重）。
 */
import { describe, expect, it } from 'vitest'
import {
  appendTurn,
  liveAssistant,
  liveTrace,
  mergeServerHistory,
  newAssistantMessage,
  newUserMessage,
  withAssistantStatus,
  withAssistantText,
  withAssistantVersion,
  withTraceEntry,
} from '../chatTranscript'
import type { ChatMessage } from '../chatTranscript'
import type { ConversationMessage } from '../../types/analysis'

const AT = '2026-11-05T12:00:00Z'

function row(
  id: number,
  role: ConversationMessage['role'],
  content: string | null,
  message_type = role === 'user' ? 'text' : 'assistant_text',
  at = AT,
): ConversationMessage {
  return { id, role, content, message_type, created_at: at }
}

function sent(text = '2024年华东销售趋势'): ChatMessage[] {
  return appendTurn([], {
    userId: 'u-local',
    assistantId: 'a-local',
    text,
    createdAt: AT,
  })
}

/**
 * 落库的思考链路行（2026-11-05 plan trace-persistence）。
 * `metadata` 故意给**字符串**——线上 asyncpg 没有 jsonb codec，API 返回的就是 JSON 文本。
 */
function traceRow(
  id: number,
  at: string,
  entries: Array<Record<string, unknown>>,
): ConversationMessage {
  return {
    id,
    role: 'assistant',
    content: null,
    message_type: 'turn_trace',
    created_at: at,
    metadata: JSON.stringify({ entries, duration_ms: 3000, status: 'done' }),
  }
}

describe('appendTurn — 发送即追加', () => {
  it('一次追加用户消息 + 空的助手占位', () => {
    const messages = sent()
    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({ role: 'user', content: '2024年华东销售趋势', status: 'done' })
    expect(messages[1]).toMatchObject({ role: 'assistant', content: '', status: 'streaming' })
    expect(messages[0].serverId).toBeNull()
  })
})

describe('mergeServerHistory — 按 id 去重，绝不整表覆盖', () => {
  it('服务端还没有这轮时，本地两条原样保留', () => {
    const messages = sent()
    expect(mergeServerHistory(messages, [])).toEqual(messages)
    expect(mergeServerHistory(messages, null)).toEqual(messages)
  })

  it('服务端出现同一句用户消息 → 认领 serverId，不重复追加', () => {
    const merged = mergeServerHistory(sent(), [row(11, 'user', '2024年华东销售趋势')])
    expect(merged).toHaveLength(2)
    expect(merged[0].id).toBe('u-local')
    expect(merged[0].serverId).toBe(11)
  })

  it('assistant_text 到达时回填到同一条空占位（不新增助手消息）', () => {
    const merged = mergeServerHistory(sent(), [
      row(11, 'user', '2024年华东销售趋势'),
      row(12, 'assistant', '好，我理解为：2024年华东的销售额。'),
    ])
    expect(merged).toHaveLength(2)
    expect(merged[1].id).toBe('a-local')
    expect(merged[1].serverId).toBe(12)
    expect(merged[1].content).toBe('好，我理解为：2024年华东的销售额。')
  })

  it('重复合并同一批历史是幂等的', () => {
    const server = [
      row(11, 'user', '2024年华东销售趋势'),
      row(12, 'assistant', '好，我理解为：2024年华东的销售额。'),
    ]
    const once = mergeServerHistory(sent(), server)
    const twice = mergeServerHistory(once, server)
    expect(twice).toHaveLength(2)
    expect(twice.map((m) => m.serverId)).toEqual([11, 12])
  })

  it('刷新历史不会刷掉正在流的那条（上一轮 + 本轮共存）', () => {
    // 上一轮已经落地（服务端有 U1/A1），本轮刚发送（本地 U2/A2 还没有 id）
    const previous = mergeServerHistory(sent('问题一'), [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '好，我理解为：问题一。'),
    ])
    const withSecond = appendTurn(previous, {
      userId: 'u-2',
      assistantId: 'a-2',
      text: '改成按月的吧',
      createdAt: '2026-11-05T12:05:00Z',
    })
    const refreshed = mergeServerHistory(withSecond, [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '好，我理解为：问题一。'),
      row(3, 'user', '改成按月的吧'),
    ])
    expect(refreshed.map((m) => m.content)).toEqual([
      '问题一',
      '好，我理解为：问题一。',
      '改成按月的吧',
      '', // 本轮的助手占位还在
    ])
    expect(refreshed[2].serverId).toBe(3)
  })

  it('system / report_version 指针行不进对话账本', () => {
    const merged = mergeServerHistory(sent(), [
      row(11, 'user', '2024年华东销售趋势'),
      row(12, 'system', null, 'requirement_patch'),
      row(13, 'assistant', '报告 v1 已生成：华东趋势', 'report_version'),
    ])
    expect(merged).toHaveLength(2)
  })

  it('打开老会话：服务端历史全部落成 done 消息', () => {
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '好，我理解为：问题一。'),
      row(3, 'user', '问题二'),
    ])
    expect(merged.map((m) => [m.role, m.status, m.serverId])).toEqual([
      ['user', 'done', 1],
      ['assistant', 'done', 2],
      ['user', 'done', 3],
    ])
    expect(merged[0].id).toBe('srv-1')
  })

  it('本地闲聊回复（服务端没落库）在合并后仍然保留', () => {
    let messages = sent('你好')
    messages = withAssistantText(messages, '你好！有什么可以帮你的？')
    messages = withAssistantStatus(messages, 'done')
    const merged = mergeServerHistory(messages, [row(11, 'user', '你好')])
    expect(merged).toHaveLength(2)
    expect(merged[1].content).toBe('你好！有什么可以帮你的？')
  })

  // 回归钉子（2026-11-05 实测踩到）：闲聊轮的助手消息服务端不落库，若把「未认领的本地
  // 消息」统一追加到末尾，两轮闲聊的回复会被挪到后面、错挂到下一轮头上。
  it('服务端缺助手行时，本地助手消息留在**自己那一轮**的位置', () => {
    let messages = sent('你好呀')
    messages = withAssistantText(messages, '你好！我能帮你分析数据。')
    messages = withAssistantStatus(messages, 'done')
    messages = appendTurn(messages, {
      userId: 'u-2',
      assistantId: 'a-2',
      text: '谢谢你',
      createdAt: '2026-11-05T12:06:00Z',
    })
    messages = withAssistantText(messages, '不客气！')

    const merged = mergeServerHistory(messages, [
      row(21, 'user', '你好呀'),
      row(22, 'user', '谢谢你'),
    ])
    expect(merged.map((m) => `${m.role}:${m.content}`)).toEqual([
      'user:你好呀',
      'assistant:你好！我能帮你分析数据。',
      'user:谢谢你',
      'assistant:不客气！',
    ])
  })

  it('服务端新行按时间插回正确位置（不一律追加到末尾）', () => {
    let messages = sent('问题一')
    messages = withAssistantText(messages, '好，我理解为：问题一。')
    // 服务端回灌了一个**更早**的轮次（例如本页从未见过的历史）
    const earlier = '2026-11-05T11:00:00Z'
    const merged = mergeServerHistory(messages, [
      row(1, 'user', '老问题', 'text', earlier),
      row(2, 'assistant', '老回答', 'assistant_text', earlier),
      row(31, 'user', '问题一'),
      row(32, 'assistant', '好，我理解为：问题一。'),
    ])
    expect(merged.map((m) => m.content)).toEqual([
      '老问题',
      '老回答',
      '问题一',
      '好，我理解为：问题一。',
    ])
  })
})

describe('turn_trace —— 落库的思考链路（2026-11-05 plan trace-persistence）', () => {
  it('trace 行不产生消息，挂到该轮助手消息上', () => {
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '好，我理解为：问题一。'),
      traceRow(3, '2026-11-05T12:00:05Z', [
        { nodeName: '安全检查', status: 'success', detail: '通过（LOW）', ts: 1000 },
        { nodeName: '理解意图 · 思考', status: 'success', reasoning: '模型原文一句', ts: 4000 },
      ]),
    ])
    expect(merged.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(merged[1].trace.map((entry) => entry.nodeName)).toEqual(['安全检查', '理解意图 · 思考'])
    expect(merged[1].trace[1].reasoning).toBe('模型原文一句')
    expect(merged[1].trace[0].detail).toBe('通过（LOW）')
  })

  it('metadata 是 JSON 字符串也能读（asyncpg 无 jsonb codec）', () => {
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '答一'),
      traceRow(3, '2026-11-05T12:00:05Z', [{ nodeName: '解析需求', status: 'success', ts: 1 }]),
    ])
    expect(merged[1].trace).toHaveLength(1)
  })

  it('坏 metadata / 缺字段不炸，也不产生空步骤', () => {
    const broken: ConversationMessage = {
      id: 9,
      role: 'assistant',
      content: null,
      message_type: 'turn_trace',
      created_at: '2026-11-05T12:00:05Z',
      metadata: '{not json',
    }
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '答一'),
      broken,
      traceRow(10, '2026-11-05T12:00:09Z', [{ status: 'success', ts: 1 }]),
    ])
    expect(merged[1].trace).toEqual([])
  })

  it('同一轮两行 trace（需求阶段 + 执行阶段）合并成一条链路', () => {
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '好，我理解为：问题一。'),
      traceRow(3, '2026-11-05T12:00:05Z', [{ nodeName: '解析需求', status: 'success', ts: 1000 }]),
      traceRow(4, '2026-11-05T12:01:30Z', [
        { nodeName: '规划查询', status: 'success', ts: 2000 },
        { nodeName: '生成 SQL', status: 'success', ts: 3000 },
      ]),
    ])
    expect(merged[1].trace.map((entry) => entry.nodeName)).toEqual([
      '解析需求',
      '规划查询',
      '生成 SQL',
    ])
  })

  it('本地实时 trace 优先，重复合并不翻倍（幂等）', () => {
    let messages = sent('问题一')
    messages = withTraceEntry(messages, {
      id: 'live-解析需求',
      nodeName: '解析需求',
      status: 'success',
      detail: '本地更全',
      timestamp: 9999,
    })
    const server = [
      row(1, 'user', '问题一'),
      traceRow(2, '2026-11-05T12:00:05Z', [
        { nodeName: '解析需求', status: 'success', detail: '库里旧数据', ts: 1000 },
      ]),
    ]
    const once = mergeServerHistory(messages, server)
    const twice = mergeServerHistory(once, server)
    expect(once[1].trace).toHaveLength(1)
    expect(once[1].trace[0].detail).toBe('本地更全')
    expect(twice[1].trace).toHaveLength(1)
  })

  it('trace 行挂给它**紧邻的那一轮**，不会串到别的轮次', () => {
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '答一'),
      traceRow(3, '2026-11-05T12:00:05Z', [{ nodeName: '解析需求', status: 'success', ts: 1000 }]),
      row(4, 'user', '问题二'),
      row(5, 'assistant', '答二'),
    ])
    expect(merged[1].trace.map((entry) => entry.nodeName)).toEqual(['解析需求'])
    expect(merged[3].trace).toEqual([])
  })

  it('同一轮里两行 trace 都归该轮（需求阶段 + 执行阶段），不串下一轮', () => {
    const merged = mergeServerHistory([], [
      row(1, 'user', '问题一'),
      row(2, 'assistant', '答一'),
      traceRow(3, '2026-11-05T12:00:05Z', [{ nodeName: '解析需求', status: 'success', ts: 1000 }]),
      traceRow(4, '2026-11-05T12:01:05Z', [{ nodeName: '生成 SQL', status: 'success', ts: 2000 }]),
      row(5, 'user', '问题二'),
      row(6, 'assistant', '答二'),
    ])
    expect(merged[1].trace.map((entry) => entry.nodeName)).toEqual(['解析需求', '生成 SQL'])
    expect(merged[3].trace).toEqual([])
  })
})

describe('助手消息上的过程与版本', () => {
  it('trace 按 nodeName 去重，只写最后一条助手消息', () => {
    let messages = sent()
    messages = withTraceEntry(messages, { id: '1', nodeName: '解析需求', status: 'running', timestamp: 1 })
    messages = withTraceEntry(messages, { id: '2', nodeName: '解析需求', status: 'success', timestamp: 2 })
    expect(liveTrace(messages)).toHaveLength(1)
    expect(liveTrace(messages)[0].status).toBe('success')
    expect(messages[0].trace).toEqual([])
  })

  it('版本挂到最后一条助手消息', () => {
    const messages = withAssistantVersion(sent(), 4)
    expect(liveAssistant(messages)?.version).toBe(4)
  })

  it('没有助手消息时更新动作不炸（历史只读）', () => {
    const onlyUser = [newUserMessage({ userId: 'u1', text: 'q', createdAt: AT })]
    expect(withAssistantText(onlyUser, 'x')).toEqual(onlyUser)
    expect(withAssistantStatus(onlyUser, 'done')).toEqual(onlyUser)
    expect(newAssistantMessage({ assistantId: 'a1', createdAt: AT }).status).toBe('streaming')
  })
})
