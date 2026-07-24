import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createTemplate,
  deleteTemplate,
  fetchTemplates,
  renameTemplate,
} from '../templatesClient'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  localStorage.clear()
  vi.restoreAllMocks()
})

function mockJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeFetch(status = 200, body: unknown = {}): ReturnType<typeof vi.fn> {
  return vi.fn<typeof fetch>(async () =>
    mockJsonResponse(body, status) as unknown as ReturnType<typeof fetch>,
  )
}

describe('templatesClient', () => {
  it('fetchTemplates returns parsed JSON', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk' } }))
    globalThis.fetch = makeFetch(200, {
      templates: [{ id: 1, user_id: 1, name: 't1', description: '', requirement_payload: { k: 'v' } }],
    }) as unknown as typeof fetch
    const out = await fetchTemplates()
    expect(out.templates).toHaveLength(1)
    expect(out.templates[0].name).toBe('t1')
  })

  it('createTemplate POSTs with Bearer header when token present', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk' } }))
    const fetchMock = makeFetch(200, {
      template: { id: 1, user_id: 1, name: 'n', description: '', requirement_payload: {} },
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch
    await createTemplate('n', '', { k: 'v' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    expect(url).toBe('/api/v1/templates')
    expect(init?.method).toBe('POST')
    const headers = (init?.headers ?? {}) as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tk')
    expect(JSON.parse(init?.body as string)).toEqual({
      name: 'n', description: '', requirement_payload: { k: 'v' },
    })
  })

  it('renameTemplate PATCHes the right URL', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk' } }))
    const fetchMock = makeFetch(200, {
      template: { id: 1, user_id: 1, name: 'new', description: '', requirement_payload: {} },
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch
    await renameTemplate(1, 'new')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    expect(url).toBe('/api/v1/templates/1')
    expect(init?.method).toBe('PATCH')
  })

  it('deleteTemplate DELETEs the right URL', async () => {
    localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tk' } }))
    const fetchMock = makeFetch(200, { deleted: true })
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const out = await deleteTemplate(7)
    expect(out.deleted).toBe(true)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?]
    expect(url).toBe('/api/v1/templates/7')
    expect(init?.method).toBe('DELETE')
  })
})
