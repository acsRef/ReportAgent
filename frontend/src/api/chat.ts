import { parseSSEChunk } from './sse'
import type { SSEEvent, ReportResponse } from '../types/report'
import { useAuthStore } from '../stores/authStore'

type EventHandler = (event: SSEEvent) => void

export async function chatStream(
  userQuery: string,
  sessionId: string,
  onEvent: EventHandler,
  signal?: AbortSignal,
): Promise<void> {
  const controller = new AbortController()
  // Long timeout — MiniMax LLM can take 60-120s
  const timeoutId = setTimeout(() => controller.abort(), 180000)

  const combinedSignal = signal
    ? combineSignals(signal, controller.signal)
    : controller.signal

  const { token } = useAuthStore.getState()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  let response: Response
  try {
    response = await fetch('/api/v1/chat', {
      method: 'POST',
      headers,
      body: JSON.stringify({ user_query: userQuery, session_id: sessionId }),
      signal: combinedSignal,
    })
  } catch (err) {
    clearTimeout(timeoutId)
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('请求超时（180秒），请重试')
    }
    throw err
  }
  clearTimeout(timeoutId)

  if (response.status === 401) {
    useAuthStore.getState().logout()
    window.location.href = '/login'
    throw new Error('登录已过期，请重新登录')
  }

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    while (true) {
      const boundary = buffer.indexOf('\n\n')
      if (boundary === -1) break

      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)

      const trimmed = chunk.trim()
      if (!trimmed) continue

      const events = parseSSEChunk(trimmed)
      for (const ev of events) {
        onEvent(ev)
      }
    }
  }

  const remaining = buffer.trim()
  if (remaining) {
    const events = parseSSEChunk(remaining)
    for (const ev of events) {
      onEvent(ev)
    }
  }
}

function combineSignals(...signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController()
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason)
      return controller.signal
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  return controller.signal
}

export function parseReportData(data: string): ReportResponse | null {
  try {
    return JSON.parse(data) as ReportResponse
  } catch {
    return null
  }
}

export function parseTraceData(data: string): { step: string; status: string; detail?: string } | null {
  try {
    return JSON.parse(data) as { step: string; status: string; detail?: string }
  } catch {
    return null
  }
}
