import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openChat, type ChatStreamClose } from '../analysisClient'
import type { AnalysisStreamEvent } from '../analysisEvents'

function sseResponse(frames: string, status = 200): Response {
  return new Response(frames, { status, headers: { 'Content-Type': 'text/event-stream' } })
}

interface Harness {
  events: AnalysisStreamEvent[]
  onEvent: (e: AnalysisStreamEvent) => void
  onClose: (info: ChatStreamClose) => void
  /** onClose 被调用时 resolve——单测据此等异步 IIFE 收尾。 */
  closed: Promise<ChatStreamClose>
}

function harness(): Harness {
  const events: AnalysisStreamEvent[] = []
  let resolve: (info: ChatStreamClose) => void = () => {}
  const closed = new Promise<ChatStreamClose>((r) => {
    resolve = r
  })
  return {
    events,
    onEvent: (e) => events.push(e),
    onClose: (info) => resolve(info),
    closed,
  }
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tok-1' } }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('openChat 流收尾契约（2026-11-03 断流兜底）', () => {
  it('收到终态事件 → onClose.sawTerminal = true', async () => {
    const frames =
      'event: phase\ndata: {"phase":"parsing"}\n\n' +
      'event: requirement\ndata: {"id":"d1","version":1,"status":"complete","summary":"华东","missing_fields":[]}\n\n' +
      'event: done\ndata: {"final_phase":"awaiting_confirm"}\n\n'
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(frames)))
    const h = harness()

    const controller = openChat({ user_query: '只看华东', mode: 'supplement' }, h.onEvent, undefined, h.onClose)
    const info = await h.closed

    expect(controller.signal.aborted).toBe(false)
    expect(info).toEqual({ sawTerminal: true, aborted: false })
    expect(h.events.map((e: AnalysisStreamEvent) => e.type)).toEqual([
      'phase',
      'requirement',
      'done',
    ])
  })

  it('只有 phase、流静默结束 → sawTerminal = false（调用方必须自己解锁 + 报错）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse('event: phase\ndata: {"phase":"parsing"}\n\n')),
    )
    const h = harness()

    openChat({ user_query: '只看华东', mode: 'supplement' }, h.onEvent, undefined, h.onClose)
    const info = await h.closed

    expect(info).toEqual({ sawTerminal: false, aborted: false })
    expect(h.events.map((e: AnalysisStreamEvent) => e.type)).toEqual(['phase'])
  })

  it('409 SESSION_BUSY → 明确错误事件（不再落成 HTTP_ERROR: 409）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse('', 409)))
    const h = harness()

    openChat({ user_query: 'q', mode: 'supplement' }, h.onEvent, undefined, h.onClose)
    const info = await h.closed

    const err = h.events[0] as Extract<AnalysisStreamEvent, { type: 'error' }>
    expect(err.type).toBe('error')
    expect(err.error.code).toBe('SESSION_BUSY')
    expect(err.error.recoverable).toBe(true)
    expect(err.error.failed_action).toBe('supplement')
    expect(info.sawTerminal).toBe(true)
  })

  it('HTTP 500 → HTTP_ERROR 事件 + sawTerminal = true', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse('', 500)))
    const h = harness()

    openChat({ user_query: 'q', mode: 'new' }, h.onEvent, undefined, h.onClose)
    const info = await h.closed

    const err = h.events[0] as Extract<AnalysisStreamEvent, { type: 'error' }>
    expect(err.error.code).toBe('HTTP_ERROR')
    expect(info.sawTerminal).toBe(true)
  })

  it('被 abort（停止按钮）→ aborted = true，调用方不应再报连接中断', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () => {
              const err = new Error('aborted')
              err.name = 'AbortError'
              reject(err)
            })
          }),
      ),
    )
    const h = harness()

    const controller = openChat({ user_query: 'q', mode: 'new' }, h.onEvent, undefined, h.onClose)
    controller.abort()
    const info = await h.closed

    expect(info).toEqual({ sawTerminal: false, aborted: true })
    expect(h.events).toHaveLength(0)
  })
})
