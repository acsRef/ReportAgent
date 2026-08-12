/** useExecutionPoll（后台任务轮询）测试。intervalMs 注入小值，用真实 timers。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useExecutionPoll } from '../WorkbenchPage'
import { fetchSession } from '../../api/sessionsClient'

vi.mock('../../api/sessionsClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/sessionsClient')>()
  return { ...actual, fetchSession: vi.fn() }
})

const fetchSessionMock = vi.mocked(fetchSession)

function snapshot(phase: string) {
  return {
    session: { phase, report_versions: [] },
    messages: [],
    current_requirement: null,
    latest_report: null,
    last_failed_action: null,
  } as any
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))

afterEach(() => {
  fetchSessionMock.mockReset()
})

describe('useExecutionPoll', () => {
  it('polls while generating, calls onDone once when report_ready, then stops', async () => {
    const onDone = vi.fn()
    let phase = 'generating'
    fetchSessionMock.mockImplementation(async () => snapshot(phase))

    renderHook(() => useExecutionPoll('sid-1', true, onDone, 10))
    await waitFor(() => expect(fetchSessionMock).toHaveBeenCalled())
    await delay(30)
    expect(onDone).not.toHaveBeenCalled()

    phase = 'report_ready'
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    const callsAtDone = fetchSessionMock.mock.calls.length
    await delay(50)
    // 完成即停：不再轮询
    expect(fetchSessionMock.mock.calls.length).toBe(callsAtDone)
  })

  it('error phase also finishes polling', async () => {
    const onDone = vi.fn()
    fetchSessionMock.mockImplementation(async () => snapshot('error'))
    renderHook(() => useExecutionPoll('sid-1', true, onDone, 10))
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
  })

  it('does not poll when inactive', async () => {
    renderHook(() => useExecutionPoll('sid-1', false, vi.fn(), 10))
    await delay(50)
    expect(fetchSessionMock).not.toHaveBeenCalled()
  })

  it('does not poll without a sessionId', async () => {
    renderHook(() => useExecutionPoll(null, true, vi.fn(), 10))
    await delay(50)
    expect(fetchSessionMock).not.toHaveBeenCalled()
  })

  it('poll failure is silent and retries next round', async () => {
    const onDone = vi.fn()
    fetchSessionMock.mockRejectedValueOnce(new Error('network down'))
    fetchSessionMock.mockImplementation(async () => snapshot('report_ready'))
    renderHook(() => useExecutionPoll('sid-1', true, onDone, 10))
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
  })
})