import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createSendTimeoutGuard } from '../sendTimeout'

describe('createSendTimeoutGuard', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('fires the callback after the timeout elapses', () => {
    const guard = createSendTimeoutGuard(60_000)
    const onTimeout = vi.fn()
    guard.arm(onTimeout)

    vi.advanceTimersByTime(59_999)
    expect(onTimeout).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(onTimeout).toHaveBeenCalledTimes(1)
  })

  it('clear() cancels a pending timeout (done/error arrived in time)', () => {
    const guard = createSendTimeoutGuard(60_000)
    const onTimeout = vi.fn()
    guard.arm(onTimeout)
    guard.clear()

    vi.advanceTimersByTime(120_000)
    expect(onTimeout).not.toHaveBeenCalled()
  })

  it('re-arming abandons the previous timer — a later request never inherits a stale timeout', () => {
    const guard = createSendTimeoutGuard(60_000)
    const first = vi.fn()
    const second = vi.fn()

    guard.arm(first)
    vi.advanceTimersByTime(30_000)
    // 第二轮在 30s 时开始（真实场景：用户在 60s 内又发了一条）
    guard.arm(second)

    vi.advanceTimersByTime(30_000) // 第一轮本应到点（t=60s）
    expect(first).not.toHaveBeenCalled()
    expect(second).not.toHaveBeenCalled()

    vi.advanceTimersByTime(30_000) // 第二轮到点（t=90s）
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('a stale callback that already queued cannot fire after clear()', () => {
    const guard = createSendTimeoutGuard(1_000)
    const onTimeout = vi.fn()
    guard.arm(onTimeout)
    // 模拟「定时器到点与 clear 同轮事件循环」：先排队回调，再 clear
    vi.advanceTimersByTime(1_000)
    guard.clear()
    expect(onTimeout).toHaveBeenCalledTimes(1)

    const afterClear = vi.fn()
    guard.arm(afterClear)
    guard.clear()
    vi.advanceTimersByTime(5_000)
    expect(afterClear).not.toHaveBeenCalled()
  })
})
