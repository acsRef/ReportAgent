/**
 * SSE v2 client for `POST /api/v1/sessions/{sid}/confirm` and `/retry`.
 *
 * Extracted from WorkbenchPage so the stream contract is unit-testable.
 * The caller owns busy-state resets (see WorkbenchPage `finally` blocks):
 * this function resolves on every exit path and never throws.
 */
import type { AnalysisPhase } from '../types/analysis'

export type ToastApi = {
  error: (m: string) => void
  success: (m: string) => void
  warning: (m: string) => void
  info: (m: string) => void
}

export type Dispatcher = (action: any) => void

export interface ConfirmStreamCtx {
  toast: ToastApi
  dispatch: Dispatcher
  setConfirming: (v: boolean) => void
  onReport: (version?: number) => void | Promise<void>
}

export async function postConfirmStream(
  sessionId: string,
  ctx: ConfirmStreamCtx,
  action: 'confirm' | 'retry' = 'confirm',
  signal?: AbortSignal,
): Promise<void> {
  const raw = localStorage.getItem('ragent_auth')
  const token = raw ? (JSON.parse(raw)?.state?.token ?? null) : null
  if (!token) {
    ctx.toast.error('未登录')
    return
  }
  let res: Response
  try {
    res = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}/${action}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      signal,
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') return
    ctx.toast.error(`${action} 请求失败：${String(err).slice(0, 100)}`)
    return
  }
  if (!res.ok || !res.body) {
    ctx.toast.error(`${action} 失败: ${res.status}`)
    ctx.dispatch({
      type: 'analysis/failed',
      error: { code: 'HTTP_ERROR', message: `status ${res.status}`, recoverable: false, failed_action: action },
    })
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let sawReport = false
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sepIndex
    while ((sepIndex = buffer.search(/\r\n\r\n|\n\n/)) >= 0) {
      const match = buffer.match(/\r\n\r\n|\n\n/)!
      const frame = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + match[0].length)
      const evt = parseSSEFrame(frame)
      if (!evt) continue
      if (evt.eventName === 'phase') {
        ctx.dispatch({ type: 'phase/received', phase: evt.data.phase as AnalysisPhase })
      } else if (evt.eventName === 'report') {
        sawReport = true
        await ctx.onReport(
          typeof evt.data?.version === 'number' ? evt.data.version : undefined,
        )
        ctx.dispatch({ type: 'phase/received', phase: 'report_ready' })
      } else if (evt.eventName === 'error') {
        ctx.toast.error(evt.data?.message ?? '执行失败')
        ctx.dispatch({ type: 'analysis/failed', error: evt.data })
      } else if (evt.eventName === 'done' && evt.data?.final_phase) {
        ctx.dispatch({ type: 'phase/received', phase: evt.data.final_phase as AnalysisPhase })
      }
    }
  }
  if (!sawReport) {
    ctx.toast.warning('确认完成，但未收到报告事件')
  }
}

export function parseSSEFrame(frame: string): { eventName: string; data: any } | null {
  let eventName: string | null = null
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!eventName) return null
  const dataStr = dataLines.join('\n')
  if (!dataStr) return null
  try {
    return { eventName, data: JSON.parse(dataStr) }
  } catch {
    return { eventName, data: dataStr }
  }
}
