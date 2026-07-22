import type { SSEEvent, SSEEventType } from '../types/report'

/** 解析单个 SSE 事件文本（已由调用方按 \n\n 分割） */
export function parseSSEChunk(chunk: string): SSEEvent[] {
  const lines = chunk.split('\n')
  let event: SSEEventType = 'done'
  let data = ''

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      event = line.slice(7).trim() as SSEEventType
    } else if (line.startsWith('data: ')) {
      data = data ? data + '\n' + line.slice(6) : line.slice(6)
    }
  }

  return [{ event, data }]
}
