import type { SSEEvent, SSEEventType } from '../types/report'

/** 解析单个 SSE 事件文本（已由调用方按 \n\n 分割） */
export function parseSSEChunk(chunk: string): SSEEvent[] {
  const lines = chunk.split('\n')
  let event: SSEEventType | null = null
  let data = ''

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      event = line.slice(7).trim() as SSEEventType
    } else if (line.startsWith('data: ')) {
      data = data ? data + '\n' + line.slice(6) : line.slice(6)
    }
  }

  if (!event) {
    console.warn('[SSE] Ignoring event without an event field')
    return []
  }

  return [{ event, data }]
}

/**
 * P11：transport 层拆帧——只拆 event 名 + data 原文，不改 schema。
 * `data` 保持字符串交给 parseAnalysisSSEEvent 统一 JSON.parse + 白名单校验。
 * 兼容 `\r` 行尾（sse_starlette 用 CRLF）、`event:` 后可选空格、冒号注释行。
 */
export function parseSSEFrameRaw(frame: string): { eventName: string; data: string } | null {
  let eventName: string | null = null
  const dataLines: string[] = []
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!eventName || dataLines.length === 0) return null
  return { eventName, data: dataLines.join('\n') }
}
