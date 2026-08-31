/** streamAbort（P11 Review-1 P1-2）：confirm/retry/adjust 三流共用的 abort 单例。 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { __resetStreamForTest, abortStream, armStream, hasActiveStream } from '../streamAbort'

beforeEach(() => {
  __resetStreamForTest()
})

afterEach(() => {
  __resetStreamForTest()
})

describe('streamAbort', () => {
  it('arm 后 hasActiveStream=true；abort 后=false', () => {
    const c = new AbortController()
    armStream(c)
    expect(hasActiveStream()).toBe(true)
    expect(abortStream()).toBe(true)
    expect(hasActiveStream()).toBe(false)
  })

  it('abort 真断了原 controller signal', () => {
    const c = new AbortController()
    armStream(c)
    expect(c.signal.aborted).toBe(false)
    abortStream()
    expect(c.signal.aborted).toBe(true)
  })

  it('arm 替换旧 controller → 旧被 abort', () => {
    const c1 = new AbortController()
    armStream(c1)
    const c2 = new AbortController()
    armStream(c2)
    expect(c1.signal.aborted).toBe(true)
    expect(c2.signal.aborted).toBe(false)
  })

  it('abort 时无 active → 返回 false 不抛错', () => {
    expect(abortStream()).toBe(false)
    expect(() => abortStream()).not.toThrow()
  })

  it('多次 abort：第二次返回 false（已清空）', () => {
    const c = new AbortController()
    armStream(c)
    expect(abortStream()).toBe(true)
    expect(abortStream()).toBe(false)
  })
})