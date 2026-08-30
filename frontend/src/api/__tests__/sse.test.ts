import { describe, expect, it } from 'vitest'
import { parseSSEFrameRaw } from '../sse'

describe('parseSSEFrameRaw', () => {
  it('parses event + single data line WITHOUT json.parse', () => {
    expect(parseSSEFrameRaw('event: phase\ndata: {"phase":"generating"}')).toEqual({
      eventName: 'phase',
      data: '{"phase":"generating"}',
    })
  })

  it('strips trailing \\r and joins multi data lines', () => {
    const frame = 'event: report\r\ndata: {"a":\r\ndata: "b"}\r\n'
    const r = parseSSEFrameRaw(frame)
    expect(r).toEqual({ eventName: 'report', data: '{"a":\n"b"}' })
  })

  it('tolerates spaces after colons and skips comment lines', () => {
    const r = parseSSEFrameRaw(
      ': keepalive\nevent: done\ndata: {"final_phase":"report_ready"}\n\n',
    )
    expect(r).toEqual({ eventName: 'done', data: '{"final_phase":"report_ready"}' })
  })

  it('returns null when event name missing or no data line', () => {
    expect(parseSSEFrameRaw('data: x')).toBeNull()
    expect(parseSSEFrameRaw('event: phase\n\n')).toBeNull()
    expect(parseSSEFrameRaw('')).toBeNull()
  })
})