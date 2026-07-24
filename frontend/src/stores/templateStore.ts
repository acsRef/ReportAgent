/**
 * `templateStore` — Zustand store for the PG-backed template center.
 *
 * Responsibilities:
 * 1. CRUD over `/api/v1/templates`.
 * 2. One-shot migration of the old `ragent_templates` localStorage key
 *    into PG templates. If the old key exists at first load, expose
 *    a `pendingMigration` flag and a `migrateFromLocalStorage()` action.
 *
 * Why a separate store from `analysisStore`? Templates are user-level
 * assets that exist independently of the active analysis session. A
 * separate store keeps the analysis reducer pure and avoids accidental
 * cross-pollution.
 */
import { create } from 'zustand'
import {
  createTemplate,
  deleteTemplate,
  fetchTemplates,
  renameTemplate,
  type TemplateRow,
} from '../api/templatesClient'

const LEGACY_KEY = 'ragent_templates'

interface LegacyTemplate {
  name: string
  description?: string
  config?: { requirement?: any }
}

interface TemplateStoreState {
  templates: TemplateRow[]
  loading: boolean
  error: string | null
  pendingMigration: LegacyTemplate[] | null

  refresh: () => Promise<void>
  create: (name: string, description: string, requirement_payload: any) => Promise<void>
  rename: (id: number, name: string, description?: string) => Promise<void>
  remove: (id: number) => Promise<void>
  detectLegacy: () => void
  migrateFromLocalStorage: () => Promise<{ imported: number; skipped: number }>
  dismissMigration: () => void
}

export const useTemplateStore = create<TemplateStoreState>((set, get) => ({
  templates: [],
  loading: false,
  error: null,
  pendingMigration: null,

  refresh: async () => {
    set({ loading: true, error: null })
    try {
      const { templates } = await fetchTemplates()
      set({ templates, loading: false })
    } catch (err) {
      set({ error: String(err).slice(0, 200), loading: false })
    }
  },

  create: async (name, description, requirement_payload) => {
    const { template } = await createTemplate(name, description, requirement_payload)
    set((s) => ({ templates: [template, ...s.templates] }))
  },

  rename: async (id, name, description) => {
    const { template } = await renameTemplate(id, name, description)
    set((s) => ({
      templates: s.templates.map((t) => (t.id === id ? template : t)),
    }))
  },

  remove: async (id) => {
    await deleteTemplate(id)
    set((s) => ({ templates: s.templates.filter((t) => t.id !== id) }))
  },

  detectLegacy: () => {
    if (get().pendingMigration) return // already noticed
    try {
      const raw = localStorage.getItem(LEGACY_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw) as LegacyTemplate[] | { templates?: LegacyTemplate[] }
      const list: LegacyTemplate[] = Array.isArray(parsed)
        ? parsed
        : (parsed as any)?.templates ?? []
      if (list.length > 0) {
        set({ pendingMigration: list })
      }
    } catch {
      // Ignore corrupt localStorage; users can re-create.
    }
  },

  migrateFromLocalStorage: async () => {
    const pending = get().pendingMigration
    if (!pending) return { imported: 0, skipped: 0 }
    let imported = 0
    let skipped = 0
    for (const item of pending) {
      const req = item.config?.requirement
      if (!req || typeof req !== 'object') {
        skipped += 1
        continue
      }
      try {
        await get().create(
          item.name,
          item.description ?? '',
          req,
        )
        imported += 1
      } catch {
        skipped += 1
      }
    }
    // Best-effort cleanup of the legacy key.
    try { localStorage.removeItem(LEGACY_KEY) } catch { /* ignore */ }
    set({ pendingMigration: null })
    return { imported, skipped }
  },

  dismissMigration: () => {
    set({ pendingMigration: null })
    // Leave the legacy key in place so users can re-trigger detection.
  },
}))
