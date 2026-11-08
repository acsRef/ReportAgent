/**
 * 输出过程中的中断（plan 2026-11-08-interruption-resilience）——只跑这一个文件。
 *
 * 覆盖三类现实场景：
 *   ① 连接断流（网络/服务端断开）：步骤转 error、消息转 error、给一句可读说明；
 *   ② 用户中途停止 / 切走（后台还在跑）：步骤 pending，消息仍 streaming（轮询补终态）；
 *   ③ 刷新（F5）：快照只剩 user 行时，「当轮」仍然成立（页面按 phase 显示解析中，不画空泡）；
 *      轮次结束后的快照（assistant_text + turn_trace）能把内容补回同一条消息。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { useAnalysisStore } from '../analysisStore'
import { initialAnalysisState } from '../analysisReducer'
import { buildChatTurns, currentTurnKey } from '../../components/workbench/historyTurns'
import type { ConversationMessage } from '../../types/analysis'

function sendTurn(text = '2024年各区域销售额') {
  useAnalysisStore.getState().dispatch({
    type: 'chat/turn-appended',
    turn: {
      userId: 'u-1',
      assistantId: 'a-1',
      text,
      createdAt: '2026-11-08T05:00:00Z',
    },
  })
}

function trace(step: string, status: 'running' | 'success' = 'running') {
  useAnalysisStore.getState().dispatch({
    type: 'timeline/received',
    entry: { id: `${step}-${status}`, nodeName: step, status, timestamp: Date.now() },
  })
}

function row(
  id: number,
  role: ConversationMessage['role'],
  content: string | null,
  message_type = role === 'user' ? 'text' : 'assistant_text',
  created_at = '2026-11-08T05:00:02Z',
): ConversationMessage {
  return { id, role, content, message_type, created_at }
}

beforeEach(() => {
  useAnalysisStore.setState(initialAnalysisState)
})

describe('① 连接断流（网络中断 / 服务端断开）', () => {
  it('断流收尾：步骤转 error、消息转 error、留一句可读说明', () => {
    sendTurn()
    trace('理解意图 · 思考')
    trace('解析需求')

    useAnalysisStore.getState().dispatch({ type: 'timeline/closed', reason: 'interrupted' })

    const state = useAnalysisStore.getState()
    const assistant = state.messages[1]
    expect(assistant.role).toBe('assistant')
    expect(assistant.status).toBe('error')
    expect(assistant.trace.every((entry) => entry.status === 'error')).toBe(true)
    expect(assistant.traceNote).toContain('连接中断')
  })

  it('断流后再收到后端 error：仍是 error，且说明不丢', () => {
    sendTurn()
    trace('解析需求')
    useAnalysisStore.getState().dispatch({ type: 'timeline/closed', reason: 'interrupted' })
    useAnalysisStore.getState().dispatch({ type: 'chat/status', status: 'error' })

    const assistant = useAnalysisStore.getState().messages[1]
    expect(assistant.status).toBe('error')
    expect(assistant.trace[0].status).toBe('error')
  })
})

describe('② 用户中途停止 / 切走（后台继续跑）', () => {
  it('停止：步骤 pending，消息仍是 streaming（等轮询补终态）', () => {
    sendTurn()
    trace('理解意图 · 思考')

    useAnalysisStore.getState().dispatch({ type: 'timeline/closed', reason: 'stopped' })

    const assistant = useAnalysisStore.getState().messages[1]
    expect(assistant.trace[0].status).toBe('pending')
    expect(assistant.status).toBe('streaming')
    expect(assistant.traceNote).toContain('已停止显示')
  })

  it('停止后后台跑完：done 收尾把消息标成 done', () => {
    sendTurn()
    trace('解析需求')
    useAnalysisStore.getState().dispatch({ type: 'timeline/closed', reason: 'stopped' })
    useAnalysisStore.getState().dispatch({ type: 'timeline/closed', reason: 'done' })

    expect(useAnalysisStore.getState().messages[1].status).toBe('done')
  })
})

describe('③ 刷新（F5）后的恢复', () => {
  it('中途刷新：快照只有 user 行时，当轮仍成立（页面按 phase 显示解析中，不画空气泡）', () => {
    useAnalysisStore.getState().dispatch({
      type: 'chat/merged',
      messages: [row(11, 'user', '2024年各区域销售额')],
    })

    const state = useAnalysisStore.getState()
    expect(state.messages).toHaveLength(1)
    expect(currentTurnKey(state.messages, 'parsing')).toBe('turn-srv-11')

    const turns = buildChatTurns(state.messages, [])
    expect(turns).toHaveLength(1)
    expect(turns[0].answer).toBeNull() // 还没有回答 → 页面用解析卡承载，不画空气泡
  })

  it('轮次结束后刷新：assistant_text + turn_trace 能补回同一条消息', () => {
    // 本地只有（中断时的）一问一答占位
    useAnalysisStore.getState().dispatch({
      type: 'chat/merged',
      messages: [row(11, 'user', '2024年各区域销售额')],
    })
    // 服务端补齐：助手那句话 + 思考链路
    useAnalysisStore.getState().dispatch({
      type: 'chat/merged',
      messages: [
        row(11, 'user', '2024年各区域销售额'),
        row(12, 'assistant', '好，我理解为：2024年的销售额。'),
        {
          id: 13,
          role: 'assistant',
          content: null,
          message_type: 'turn_trace',
          created_at: '2026-11-08T05:00:05Z',
          metadata: JSON.stringify({
            entries: [
              { nodeName: '安全检查', status: 'success', detail: '通过', ts: 1000 },
              { nodeName: '理解意图 · 思考', status: 'success', reasoning: '模型原文', ts: 4000 },
            ],
            duration_ms: 3000,
            status: 'done',
          }),
        },
      ],
    })

    const state = useAnalysisStore.getState()
    expect(state.messages).toHaveLength(2)
    expect(state.messages[1].content).toContain('好，我理解为')
    expect(state.messages[1].trace.map((e) => e.nodeName)).toEqual(['安全检查', '理解意图 · 思考'])

    const turns = buildChatTurns(state.messages, [])
    expect(turns[0].answer).toContain('好，我理解为')
    expect(turns[0].trace).toHaveLength(2)
  })

  it('断流后连拉两次快照：不产生重复轮次（幂等）', () => {
    sendTurn()
    const snapshot = [
      row(11, 'user', '2024年各区域销售额'),
      row(12, 'assistant', '好，我理解为：2024年的销售额。'),
    ]
    useAnalysisStore.getState().dispatch({ type: 'chat/merged', messages: snapshot })
    useAnalysisStore.getState().dispatch({ type: 'chat/merged', messages: snapshot })

    const turns = buildChatTurns(useAnalysisStore.getState().messages, [])
    expect(turns).toHaveLength(1)
    expect(useAnalysisStore.getState().messages).toHaveLength(2)
  })
})
