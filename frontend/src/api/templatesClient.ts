/**
 * REST client for `/api/v1/templates`.
 *
 * Mirrors `sessionsClient.ts` — auth header is read from the persisted
 * `ragent_auth` localStorage entry.
 */

interface AuthHeadersOptions {
  token: string | null
}

function authHeaders(opts: AuthHeadersOptions): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (opts.token) h.Authorization = `Bearer ${opts.token}`
  return h
}

function readToken(): string | null {
  try {
    const raw = localStorage.getItem('ragent_auth')
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed?.state?.token ?? null
  } catch {
    return null
  }
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { ...authHeaders({ token: readToken() }), ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${url} failed: ${res.status} ${text.slice(0, 200)}`)
  }
  return (await res.json()) as T
}

export interface TemplateRow {
  id: number
  user_id: number
  name: string
  description: string
  requirement_payload: any
  created_at: string
  updated_at: string
}

export function fetchTemplates(): Promise<{ templates: TemplateRow[] }> {
  return jsonFetch('/api/v1/templates')
}

export function createTemplate(
  name: string,
  description: string,
  requirement_payload: any,
): Promise<{ template: TemplateRow }> {
  return jsonFetch('/api/v1/templates', {
    method: 'POST',
    body: JSON.stringify({ name, description, requirement_payload }),
  })
}

export function renameTemplate(
  id: number,
  name: string,
  description?: string,
): Promise<{ template: TemplateRow }> {
  return jsonFetch(`/api/v1/templates/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ name, description }),
  })
}

export function deleteTemplate(id: number): Promise<{ deleted: true }> {
  return jsonFetch(`/api/v1/templates/${id}`, { method: 'DELETE' })
}
