import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { postConfirmStream, type ConfirmStreamCtx } from '../confirmStream'

function sseResponse(frames: string, status = 200): Response {
  return new Response(frames, { status, headers: { 'Content-Type': 'text/event-stream' } })
}

function makeCtx(): ConfirmStreamCtx & {
  toasts: Array<[string, string]>
  actions: Array<Record<string, unknown>>
  reports: number
} {
  const toasts: Array<[string, string]> = []
  const actions: Array<Record<string, unknown>> = []
  let reports = 0
  const toast = {
    error: (m: string) => toasts.push(['error', m]),
    success: (m: string) => toasts.push(['success', m]),
    warning: (m: string) => toasts.push(['warning', m]),
    info: (m: string) => toasts.push(['info', m]),
  }
  return {
    toast,
    dispatch: (a: Record<string, unknown>) => { actions.push(a) },
    setConfirming: vi.fn(),
    onReport: async () => { reports += 1 },
    toasts,
    actions,
    get reports() { return reports },
  } as any
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tok-1' } }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('postConfirmStream', () => {
  it('happy path: phase/report/done → onReport called, phase report_ready dispatched', async () => {
    const frames =
      'event: phase\ndata: {"phase":"generating"}\n\n' +
      'event: report\ndata: {"version":1}\n\n' +
      'event: done\ndata: {"final_phase":"report_ready"}\n\n'
    const fetchMock = vi.fn(async (_input: any) => sseResponse(frames))
    vi.stubGlobal('fetch', fetchMock)

    const ctx = makeCtx()
    await postConfirmStream('sid-1', ctx)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/v1/sessions/sid-1/confirm')
    expect(ctx.reports).toBe(1)
    expect(ctx.actions).toContainEqual({ type: 'phase/received', phase: 'generating' })
    expect(ctx.actions).toContainEqual({ type: 'phase/received', phase: 'report_ready' })
    expect(ctx.toasts).toEqual([])
  })

  it('server error event → analysis/failed dispatched + toast', async () => {
    const frames =
      'event: error\ndata: {"code":"QUERY_FAILED","message":"查询未返回数据","recoverable":true,"failed_action":"confirm"}\n\n' +
      'event: done\ndata: {"final_phase":"error"}\n\n'
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(frames)))

    const ctx = makeCtx()
    await postConfirmStream('sid-1', ctx)

    expect(ctx.actions.some((a) => a.type === 'analysis/failed')).toBe(true)
    expect(ctx.toasts.some(([level, msg]) => level === 'error' && msg === '查询未返回数据')).toBe(true)
    expect(ctx.reports).toBe(0)
  })

  it('HTTP !ok → toast + analysis/failed with failed_action', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse('', 500)))
    const ctx = makeCtx()
    await postConfirmStream('sid-1', ctx)

    expect(ctx.toasts.some(([level, msg]) => level === 'error' && msg.includes('500'))).toBe(true)
    expect(ctx.actions).toContainEqual({
      type: 'analysis/failed',
      error: { code: 'HTTP_ERROR', message: 'status 500', recoverable: false, failed_action: 'confirm' },
    })
  })

  it('network failure → toast, resolves without throwing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('ECONNREFUSED') }))
    const ctx = makeCtx()
    await expect(postConfirmStream('sid-1', ctx)).resolves.toBeUndefined()
    expect(ctx.toasts.some(([level]) => level === 'error')).toBe(true)
  })

  it('missing token → toast 未登录 and fetch never called', async () => {
    localStorage.clear()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const ctx = makeCtx()
    await postConfirmStream('sid-1', ctx)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(ctx.toasts.some(([, msg]) => msg === '未登录')).toBe(true)
  })

  it('action=retry hits the /retry endpoint and tags failed_action', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse('', 502)))
    const ctx = makeCtx()
    await postConfirmStream('sid-9', ctx, 'retry')
    const failed = ctx.actions.find((a) => a.type === 'analysis/failed') as any
    expect(failed.error.failed_action).toBe('retry')
  })

  it('stream ends without report → warning toast', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse('event: done\ndata: {"final_phase":"report_ready"}\n\n')))
    const ctx = makeCtx()
    await postConfirmStream('sid-1', ctx)
    expect(ctx.toasts.some(([level]) => level === 'warning')).toBe(true)
  })

  it('409 → busy warning toast, no analysis/failed, fetch still called', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse('', 409)))
    const ctx = makeCtx()
    await postConfirmStream('sid-1', ctx)

    expect(ctx.toasts.some(([level, msg]) => level === 'warning' && msg.includes('后台生成中'))).toBe(true)
    expect(ctx.actions.some((a) => a.type === 'analysis/failed')).toBe(false)
    expect(ctx.reports).toBe(0)
  })

  it('mid-stream abort (停止/断连) → resolves silently, no toast, no throw', async () => {
    const abortError = new DOMException('The user aborted a request.', 'AbortError')
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('event: phase\ndata: {"phase":"generating"}\n\n'))
        controller.error(abortError)
      },
    })
    const res = new Response(stream as any, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
    vi.stubGlobal('fetch', vi.fn(async () => res))

    const ctx = makeCtx()
    await expect(postConfirmStream('sid-1', ctx)).resolves.toBeUndefined()
    expect(ctx.toasts).toEqual([])
    expect(ctx.actions.some((a) => a.type === 'analysis/failed')).toBe(false)
  })

  it('mid-stream non-abort error still propagates', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.error(new Error('stream exploded'))
      },
    })
    const res = new Response(stream as any, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
    vi.stubGlobal('fetch', vi.fn(async () => res))

    const ctx = makeCtx()
    await expect(postConfirmStream('sid-1', ctx)).rejects.toThrow('stream exploded')
  })
})
