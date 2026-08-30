/**
 * SSE v2 client for `/api/v1/chat`.
 *
 * Uses `fetch` + ReadableStream because EventSource doesn't support POST.
 * Buffers partial chunks and dispatches each fully-parsed event to the
 * provided `onEvent` callback.
 *
 * Event types emitted (per docs/sse-v2.md) — all parsed by the single
 * contract layer `parseAnalysisSSEEvent`:
 *   - phase { phase, reason? }
 *   - requirement (full RequirementCard)
 *   - trace { step, status, detail?, kind? }  (P11 progress family)
 *   - thinking { phase?, text? }
 *   - report { version, parent_version, title, answer } | { answer:{text} } (chitchat)
 *   - error { code, message, recoverable, failed_action, kind?, sql? }
 *   - done { final_phase }
 *
 * Legacy events (card / clarify / token) are NOT emitted by the new
 * backend flow; if we ever re-enable /api/v1/chat?mode=legacy those will
 * appear here too. The parser drops unknown event names.
 */
import type { AnalysisStreamEvent } from './analysisEvents'
import { parseAnalysisSSEEvent } from './analysisEvents'
import { parseSSEFrameRaw } from './sse'
import { handleUnauthorized } from './unauthorized'

export interface ChatRequest {
  user_query: string
  session_id?: string | null
  mode?: 'new' | 'supplement' | 'adjust' | 'legacy'
  base_report_version?: number | null
}

function getAuthToken(): string | null {
  try {
    const raw = localStorage.getItem('ragent_auth')
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed?.state?.token ?? null
  } catch {
    return null
  }
}

/**
 * Open a streaming chat request. Returns an AbortController for the
 * caller to close the stream when done (e.g. on component unmount).
 */
export function openChat(
  request: ChatRequest,
  onEvent: (event: AnalysisStreamEvent) => void,
  externalSignal?: AbortSignal,
): AbortController {
  const controller = new AbortController()
  if (externalSignal) {
    externalSignal.addEventListener('abort', () => controller.abort())
  }

  const body = JSON.stringify({
    user_query: request.user_query,
    session_id: request.session_id ?? undefined,
    mode: request.mode ?? 'new',
    base_report_version: request.base_report_version ?? undefined,
  })

  const token = getAuthToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  void (async () => {
    try {
      const res = await fetch('/api/v1/chat', {
        method: 'POST',
        headers,
        body,
        signal: controller.signal,
      })
      if (res.status === 401) {
        handleUnauthorized()
        return
      }
      if (!res.ok || !res.body) {
        onEvent({
          type: 'error',
          error: {
            code: 'HTTP_ERROR',
            message: `chat request failed: ${res.status}`,
            recoverable: false,
            failed_action: request.mode && request.mode !== 'legacy' ? request.mode : 'new',
          },
        })
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE frames are separated by a blank line. sse_starlette emits
        // them as `\r\n\r\n` per the SSE spec, but proxies may strip
        // carriage returns. Accept either.
        let sepIndex
        while ((sepIndex = buffer.search(/\r\n\r\n|\n\n/)) >= 0) {
          const match = buffer.match(/\r\n\r\n|\n\n/)!
          const frame = buffer.slice(0, sepIndex)
          buffer = buffer.slice(sepIndex + match[0].length)
          // P11：transport（parseSSEFrameRaw）→ schema（parseAnalysisSSEEvent）。
          const raw = parseSSEFrameRaw(frame)
          if (!raw) continue
          const evt = parseAnalysisSSEEvent({ event: raw.eventName as any, data: raw.data })
          if (evt) onEvent(evt)
        }
      }
    } catch (err) {
      if ((err as any)?.name === 'AbortError') return
      onEvent({
        type: 'error',
        error: {
          code: 'NETWORK_ERROR',
          message: String(err).slice(0, 300),
          recoverable: false,
          failed_action: request.mode && request.mode !== 'legacy' ? request.mode : 'new',
        },
      })
    }
  })()

  return controller
}