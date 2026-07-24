import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTemplateStore } from '../templateStore'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
  localStorage.clear()
  vi.restoreAllMocks()
})

beforeEach(() => {
  // Reset store between tests
  useTemplateStore.setState({
    templates: [],
    loading: false,
    error: null,
    pendingMigration: null,
  })
})

function mockJsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('templateStore legacy migration', () => {
  it('detects pending migration from ragent_templates localStorage', () => {
    localStorage.setItem(
      'ragent_templates',
      JSON.stringify([
        { name: 'old1', description: 'd1', config: { requirement: { id: 'r1' } } },
        { name: 'old2', config: { requirement: { id: 'r2' } } },
        { name: 'broken' }, // missing config.requirement
      ]),
    )
    useTemplateStore.getState().detectLegacy()
    expect(useTemplateStore.getState().pendingMigration).toHaveLength(3)
  })

  it('does not detect when no legacy key', () => {
    useTemplateStore.getState().detectLegacy()
    expect(useTemplateStore.getState().pendingMigration).toBeNull()
  })

  it('migrates valid entries and counts skipped', async () => {
    localStorage.setItem(
      'ragent_templates',
      JSON.stringify([
        { name: 'ok', config: { requirement: { id: 'r1' } } },
        { name: 'bad' },
      ]),
    )
    useTemplateStore.getState().detectLegacy()
    let createCalls = 0
    globalThis.fetch = vi.fn<typeof fetch>(async () => {
      createCalls += 1
      return mockJsonResponse({
        template: { id: createCalls, user_id: 1, name: 't', description: '', requirement_payload: {} },
      }) as unknown as ReturnType<typeof fetch>
    }) as unknown as typeof fetch
    const result = await useTemplateStore.getState().migrateFromLocalStorage()
    expect(result.imported).toBe(1)
    expect(result.skipped).toBe(1)
    expect(useTemplateStore.getState().pendingMigration).toBeNull()
    expect(localStorage.getItem('ragent_templates')).toBeNull()
  })

  it('dismissMigration clears the flag but keeps legacy key', () => {
    localStorage.setItem(
      'ragent_templates',
      JSON.stringify([{ name: 'old', config: { requirement: { id: 'r' } } }]),
    )
    useTemplateStore.getState().detectLegacy()
    expect(useTemplateStore.getState().pendingMigration).not.toBeNull()
    useTemplateStore.getState().dismissMigration()
    expect(useTemplateStore.getState().pendingMigration).toBeNull()
    expect(localStorage.getItem('ragent_templates')).not.toBeNull()
  })
})
