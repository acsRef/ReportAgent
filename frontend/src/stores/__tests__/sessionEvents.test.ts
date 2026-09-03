/** sessionEvents（事件→store 分发 + session 恢复）测试。P11 F6：恢复真实 phase + busy 检测。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'
import { loadSessionSnapshot, refreshVersionsAndSelectLatest, handleSSEEvent } from '../sessionEvents'
import { useAnalysisStore } from '../analysisStore'
import { initialAnalysisState } from '../analysisReducer'
import { fetchSession } from '../../api/sessionsClient'

vi.mock('../../api/sessionsClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/sessionsClient')>()
  return { ...actual, fetchSession: vi.fn() }
})

const fetchSessionMock = vi.mocked(fetchSession)

function snapshot(phase: string, reportVersions: any[] = []) {
  return {
    session: { phase, report_versions: reportVersions },
    messages: [],
    current_requirement: null,
    latest_report: null,
    last_failed_action: null,
  } as any
}

const noopToast = {
  error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn(),
} as any

beforeEach(() => {
  useAnalysisStore.setState(initialAnalysisState)
  fetchSessionMock.mockReset()
})

describe('loadSessionSnapshot', () => {
  it('resume 恢复 requirement + versions + 终态 phase', async () => {
    fetchSessionMock.mockResolvedValue({
      ...snapshot('report_ready', [
        { version: 1, title: 'r1', status: 'done', created_at: 'a', favorite: false },
        { version: 2, title: 'r2', status: 'done', created_at: 'b', favorite: false },
      ]),
      current_requirement: { id: 'req', payload: { id: 'req', version: 1, status: 'complete', summary: '华东', missing_fields: [] } },
    } as any)
    const busy = await loadSessionSnapshot('s1', noopToast)
    expect(busy).toBe(false)
    const s = useAnalysisStore.getState()
    expect(s.requirement?.id).toBe('req')
    expect(s.reportVersions.map((r) => r.version)).toEqual([1, 2])
    expect(s.phase).toBe('report_ready')
  })

  it('resume awaiting_missing 会话停在 awaiting_missing（不被 report 副作用覆盖）', async () => {
    fetchSessionMock.mockResolvedValue(snapshot('awaiting_missing'))
    await loadSessionSnapshot('s1', noopToast)
    expect(useAnalysisStore.getState().phase).toBe('awaiting_missing')
  })

  it('resume generating 返回 busy=true', async () => {
    fetchSessionMock.mockResolvedValue(snapshot('generating'))
    const busy = await loadSessionSnapshot('s1', noopToast)
    expect(busy).toBe(true)
  })

  it('resume error 返回 busy=false 且 phase=error', async () => {
    fetchSessionMock.mockResolvedValue(snapshot('error'))
    const busy = await loadSessionSnapshot('s1', noopToast)
    expect(busy).toBe(false)
    expect(useAnalysisStore.getState().phase).toBe('error')
  })

  it('非法 phase 不覆写（防御）', async () => {
    fetchSessionMock.mockResolvedValue(snapshot('fizzled'))
    const busy = await loadSessionSnapshot('s1', noopToast)
    expect(busy).toBe(false)
    expect(useAnalysisStore.getState().phase).toBe('idle')
  })

  it('加载失败 → toast + busy=false', async () => {
    fetchSessionMock.mockRejectedValue(new Error('network'))
    const busy = await loadSessionSnapshot('s1', noopToast)
    expect(busy).toBe(false)
    expect(noopToast.error).toHaveBeenCalled()
  })
})

describe('refreshVersionsAndSelectLatest', () => {
  it('拉取最新版本列表并自动选中最后一个', async () => {
    fetchSessionMock.mockResolvedValue(snapshot('report_ready', [
      { version: 1, title: 'r1', status: 'done', created_at: 'a', favorite: false },
      { version: 3, title: 'r3', status: 'done', created_at: 'c', favorite: false },
    ]))
    await refreshVersionsAndSelectLatest('s1')
    const s = useAnalysisStore.getState()
    expect(s.reportVersions.map((r) => r.version)).toEqual([1, 3])
    expect(s.selectedReportVersion).toBe(3)
  })
})

describe('handleSSEEvent（sessionEvents 版）', () => {
  it('report with version → 刷新版本（免 _dispatch 参数）', async () => {
    fetchSessionMock.mockResolvedValue(snapshot('report_ready', [
      { version: 2, title: 'r2', status: 'done', created_at: 'b', favorite: false },
    ]))
    const msgApi = { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }
    handleSSEEvent(
      { type: 'report', report: { version: 2, title: 'r2', answer: { text: 'x' } } },
      { msgApi: msgApi as any, sessionId: 's1', setSending: vi.fn(), setCasualReply: vi.fn() },
    )
    expect(msgApi.success).toHaveBeenCalledWith('报告 v2 已生成')
    await waitFor(() => expect(useAnalysisStore.getState().selectedReportVersion).toBe(2))
  })

  it('error 事件 → 刷新版本列表（FAILED 版本落库后 ReportPaper error band 才能渲染）', async () => {
    // Review-2 / spec05 known-red 修复：FAILED 落库后后端只发 error 事件，
    // 前端必须拉最新版本态并选中（status=error 的版本 → ReportPaper band）。
    fetchSessionMock.mockResolvedValue(snapshot('error', [
      { version: 1, title: 'r1', status: 'error', created_at: 'a', favorite: false },
    ]))
    const msgApi = { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }
    handleSSEEvent(
      { type: 'error', error: { code: 'SQL_EXECUTION_ERROR', message: '查询执行失败', recoverable: false, failed_action: 'confirm' } },
      { msgApi: msgApi as any, sessionId: 's1', setSending: vi.fn(), setCasualReply: vi.fn() },
    )
    expect(msgApi.error).toHaveBeenCalled()
    expect(fetchSessionMock).toHaveBeenCalled()
    await waitFor(() => expect(useAnalysisStore.getState().selectedReportVersion).toBe(1))
    expect(useAnalysisStore.getState().reportVersions[0]?.status).toBe('error')
  })
})