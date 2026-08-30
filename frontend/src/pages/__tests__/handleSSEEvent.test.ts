/** handleSSEEvent（openChat/confirm 流统一事件处理）单元测试。
 *  F5：report 带 version → 刷新版本并选中；F4：chitchat 无 version → 闲聊泡。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'
import { handleSSEEvent, type StreamEventCtx } from '../../stores/sessionEvents'
import { useAnalysisStore } from '../../stores/analysisStore'
import { initialAnalysisState } from '../../stores/analysisReducer'
import { fetchSession } from '../../api/sessionsClient'

vi.mock('../../api/sessionsClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/sessionsClient')>()
  return { ...actual, fetchSession: vi.fn() }
})

const fetchSessionMock = vi.mocked(fetchSession)

function snapshot() {
  return {
    session: {
      phase: 'report_ready',
      report_versions: [{ version: 2, title: '报告 v2', status: 'done', created_at: 'now', favorite: false }],
    },
    messages: [],
    current_requirement: null,
    latest_report: null,
    last_failed_action: null,
  } as any
}

function makeCtx(overrides?: Partial<StreamEventCtx>) {
  const msgApi = {
    error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn(),
  }
  const ctx: StreamEventCtx = {
    msgApi,
    sessionId: 'sid-1',
    setSending: vi.fn(),
    setCasualReply: vi.fn(),
    ...overrides,
  }
  return { ctx, msgApi }
}

beforeEach(() => {
  useAnalysisStore.setState(initialAnalysisState)
  fetchSessionMock.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('handleSSEEvent', () => {
  it('phase event → phase/received 提交到 store', () => {
    handleSSEEvent({ type: 'phase', phase: 'awaiting_confirm' }, makeCtx().ctx)
    expect(useAnalysisStore.getState().phase).toBe('awaiting_confirm')
  })

  it('error event → toast + analysis/failed', () => {
    const { ctx, msgApi } = makeCtx()
    handleSSEEvent(
      { type: 'error', error: { code: 'QUERY_FAILED', message: '查询失败', recoverable: true, failed_action: 'sql', kind: null, sql: null } },
      ctx,
    )
    expect(msgApi.error).toHaveBeenCalledWith('查询失败')
    expect(useAnalysisStore.getState().phase).toBe('error')
    expect(useAnalysisStore.getState().error?.code).toBe('QUERY_FAILED')
  })

  it('done event → setSending(false) + 终态 phase', () => {
    const { ctx } = makeCtx()
    handleSSEEvent({ type: 'done', finalPhase: 'report_ready' }, ctx)
    expect(ctx.setSending).toHaveBeenCalledWith(false)
    expect(useAnalysisStore.getState().phase).toBe('report_ready')
  })

  it('report with version → 刷新版本列表并选中（adjust/retry 流）', async () => {
    fetchSessionMock.mockImplementation(async () => snapshot())
    const { ctx, msgApi } = makeCtx()
    handleSSEEvent(
      { type: 'report', report: { version: 2, parent_version: 1, title: '报告', answer: { text: 'x' } } },
      ctx,
    )
    // refresh 内部先 await import() 再调 fetchSession——waitFor 等微任务
    await waitFor(() => expect(fetchSessionMock).toHaveBeenCalledWith('sid-1'))
    expect(msgApi.success).toHaveBeenCalledWith('报告 v2 已生成')
    await waitFor(() => expect(useAnalysisStore.getState().selectedReportVersion).toBe(2))
    expect(useAnalysisStore.getState().reportVersions.some((r) => r.version === 2)).toBe(true)
  })

  it('report without version (chitchat) → 闲聊泡，不触发刷新', () => {
    fetchSessionMock.mockResolvedValue(snapshot())
    const { ctx } = makeCtx()
    handleSSEEvent({ type: 'report', report: { answer: { text: '你好！有什么可以帮你的？' } } }, ctx)
    expect(ctx.setCasualReply).toHaveBeenCalledWith('你好！有什么可以帮你的？')
    expect(fetchSessionMock).not.toHaveBeenCalled()
    expect(ctx.msgApi.success).not.toHaveBeenCalled()
  })

  it('chitchat 在无 sessionId 会话也不刷新', () => {
    fetchSessionMock.mockResolvedValue(snapshot())
    const { ctx, msgApi } = makeCtx({ sessionId: null })
    handleSSEEvent({ type: 'report', report: { answer: { text: '嗨' } } }, ctx)
    expect(ctx.setCasualReply).toHaveBeenCalledWith('嗨')
    expect(fetchSessionMock).not.toHaveBeenCalled()
    expect(msgApi.success).not.toHaveBeenCalled()
  })

  it('trace event → onTrace 回调拿到 TimelineEntry', () => {
    const onTrace = vi.fn()
    const { ctx } = makeCtx({ onTrace })
    handleSSEEvent(
      { type: 'trace', entry: { id: 'x', nodeName: '生成 SQL', status: 'running', kind: 'sql', timestamp: 1 } },
      ctx,
    )
    expect(onTrace).toHaveBeenCalledWith(expect.objectContaining({ nodeName: '生成 SQL', kind: 'sql' }))
  })

  it('requirement event → store 更新需求卡', () => {
    const requirement = {
      id: 'req-1', version: 1, status: 'pending', summary: '华东对比',
      missing_fields: ['time_range'], content: [], created_at: 'now',
    } as any
    handleSSEEvent({ type: 'requirement', requirement }, makeCtx().ctx)
    expect(useAnalysisStore.getState().requirement).toEqual(requirement)
  })
})