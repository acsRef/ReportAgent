import type { SSEEvent, SSEEventType } from '../types/report'

/** 解析 SSE 文本（event: xxx\ndata: xxx\n\n 格式） */
export function parseSSEChunk(chunk: string): SSEEvent[] {
  const events: SSEEvent[] = []
  const rawEvents = chunk.split('\n\n').filter(Boolean)

  for (const raw of rawEvents) {
    const lines = raw.split('\n')
    let event: SSEEventType = 'done'
    let data = ''

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        event = line.slice(7).trim() as SSEEventType
      } else if (line.startsWith('data: ')) {
        data = data ? data + '\n' + line.slice(6) : line.slice(6)
      }
    }

    events.push({ event, data })
  }

  return events
}
