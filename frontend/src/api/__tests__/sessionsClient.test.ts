import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchSessions, fetchSession, fetchReportVersion } from '../sessionsClient'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  localStorage.clear()
})

function mockJsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('sessionsClient', () => {
  it('fetchSessions calls GET /api/v1/sessions with Bearer header when token present', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk-1' } }))
    const fetchMock = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({ sessions: [] }) as unknown as ReturnType<typeof fetch>,
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await fetchSessions()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    expect(url).toBe('/api/v1/sessions')
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tk-1')
  })

  it('fetchSession throws on 404', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk-1' } }))
    globalThis.fetch = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({}, 404) as unknown as ReturnType<typeof fetch>,
    ) as unknown as typeof fetch
    await expect(fetchSession('s-1')).rejects.toThrow(/404/)
  })

  it('fetchReportVersion returns parsed JSON', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk-1' } }))
    const report = { id: 1, version: 2, title: 'v2', report_payload: { x: 1 } }
    globalThis.fetch = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({ report }) as unknown as ReturnType<typeof fetch>,
    ) as unknown as typeof fetch
    const out = await fetchReportVersion('s-1', 2)
    expect(out.report.version).toBe(2)
    expect(out.report.title).toBe('v2')
  })

  it('omits Authorization header when no token in storage', async () => {
    localStorage.clear()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({ sessions: [] }) as unknown as ReturnType<typeof fetch>,
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch
    await fetchSessions()
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
  })
})
