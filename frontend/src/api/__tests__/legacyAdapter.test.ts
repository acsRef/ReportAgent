import { describe, expect, it } from 'vitest'
import { adaptLegacyEvent } from '../legacyAdapter'

describe('adaptLegacyEvent', () => {
  it('wraps a legacy card event in a "legacy" channel payload', () => {
    const evt = adaptLegacyEvent('card', { type: 'intent_card', payload: {} })
    expect(evt.type).toBe('legacy')
    expect(evt.data.event).toBe('card')
    expect(evt.data.payload).toEqual({ type: 'intent_card', payload: {} })
  })

  it('wraps a legacy token event', () => {
    const evt = adaptLegacyEvent('token', { text: 'hello' })
    expect(evt.type).toBe('legacy')
    expect(evt.data.event).toBe('token')
    expect(evt.data.payload.text).toBe('hello')
  })

  it('wraps a legacy clarify event', () => {
    const evt = adaptLegacyEvent('clarify', { question: '?' })
    expect(evt.type).toBe('legacy')
    expect(evt.data.event).toBe('clarify')
  })
})
