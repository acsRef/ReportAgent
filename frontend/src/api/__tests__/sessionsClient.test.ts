import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchSessions,
  fetchSession,
  fetchReportVersion,
  downloadReportXlsx,
  setReportFavorite,
} from '../sessionsClient'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  localStorage.clear()
  vi.restoreAllMocks()
})

/** jsdom 没有 createObjectURL / 导航实现——导出链路的落盘动作在这里被拦下来。 */
function stubDownload(): { blobs: Blob[]; click: ReturnType<typeof vi.fn> } {
  const blobs: Blob[] = []
  const createObjectURL = vi.fn((b: Blob) => {
    blobs.push(b)
    return 'blob:mock'
  })
  Object.defineProperty(URL, 'createObjectURL', {
    value: createObjectURL,
    configurable: true,
    writable: true,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    value: vi.fn(),
    configurable: true,
    writable: true,
  })
  const click = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(click)
  return { blobs, click }
}

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
    // Plan B (2026-08-09): 分页参数默认 limit=30&offset=0
    expect(url).toBe('/api/v1/sessions?limit=30&offset=0')
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

  it('downloadReportXlsx sends Bearer to export.xlsx and hands the blob to download', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk-1' } }))
    const { blobs, click } = stubDownload()
    const fetchMock = vi.fn<typeof fetch>(async () =>
      new Response(new Blob(['xlsx-bytes']), { status: 200 }) as unknown as ReturnType<typeof fetch>,
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await downloadReportXlsx('s-1', 3, 'report-v3.xlsx')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    // 关键：不是 window.open('/api/...')——没有 Authorization 必然 401。
    expect(url).toBe('/api/v1/sessions/s-1/reports/3/export.xlsx')
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tk-1')
    expect(blobs).toHaveLength(1)
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('downloadReportXlsx surfaces the backend detail code (NO_QUERY_SNAPSHOT)', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk-1' } }))
    stubDownload()
    globalThis.fetch = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({ detail: 'NO_QUERY_SNAPSHOT' }, 404) as unknown as ReturnType<typeof fetch>,
    ) as unknown as typeof fetch

    await expect(downloadReportXlsx('s-1', 3, 'x.xlsx')).rejects.toThrow('NO_QUERY_SNAPSHOT')
  })

  it('downloadReportXlsx logs out on 401 instead of downloading an error page', async () => {
    window.history.pushState({}, '', '/login')
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'stale' } }))
    const { blobs } = stubDownload()
    globalThis.fetch = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({ detail: 'UNAUTHORIZED' }, 401) as unknown as ReturnType<typeof fetch>,
    ) as unknown as typeof fetch

    await expect(downloadReportXlsx('s-1', 3, 'x.xlsx')).rejects.toThrow(/401/)
    expect(blobs).toHaveLength(0)
    window.history.pushState({}, '', '/')
  })

  it('setReportFavorite PATCHes the favorite flag', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk-1' } }))
    const fetchMock = vi.fn<typeof fetch>(async () =>
      mockJsonResponse({ version: 2, favorite: true }) as unknown as ReturnType<typeof fetch>,
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch

    const out = await setReportFavorite('s-1', 2, true)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    expect(url).toBe('/api/v1/sessions/s-1/reports/2/favorite')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse(String(init?.body))).toEqual({ favorite: true })
    expect(out.favorite).toBe(true)
  })
})
