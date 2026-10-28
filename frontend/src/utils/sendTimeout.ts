/**
 * 单次发送的「响应过慢」提示闸（P-17）。
 *
 * 背景：WorkbenchPage 之前用裸 `setTimeout(60_000)` 兜底，三个问题叠加——
 *   1. timer 从不清理：上一轮的 timer 到点会误判**正在进行中的下一轮**为超时；
 *   2. 超时只 `setSending(false)` 不断流：UI 说"超时"，SSE 却继续推送，
 *      用户看到"超时了但结果又出来了"；
 *   3. 解锁发送框后还能重复提交，可能诱发并发执行。
 *
 * 本模块只保留"提示"语义：调用方在超时回调里**只提示、不改状态、不断流**——
 * 任务由 SSE 自己的终态（done / error）收尾。`clear()` 同时递增序号，
 * 即使底层 timer 因 JS 事件队列延迟触发，过期回调也不会误伤新一轮。
 */
export interface SendTimeoutGuard {
  /** 重新计时（会取消上一轮未触发的定时器）。 */
  arm: (onTimeout: () => void) => void
  /** 取消计时并使所有未触发的回调失效（收到 done / error / 用户停止时调用）。 */
  clear: () => void
}

export function createSendTimeoutGuard(
  timeoutMs: number,
  setTimer: (handler: () => void, ms: number) => ReturnType<typeof setTimeout> = setTimeout,
  clearTimer: (id: ReturnType<typeof setTimeout>) => void = clearTimeout,
): SendTimeoutGuard {
  let timer: ReturnType<typeof setTimeout> | null = null
  let seq = 0

  return {
    arm(onTimeout) {
      const current = ++seq
      if (timer !== null) {
        clearTimer(timer)
        timer = null
      }
      timer = setTimer(() => {
        timer = null
        // 已被 clear() 或新一轮 arm() 取代 → 静默丢弃，绝不误伤当前请求
        if (current !== seq) return
        onTimeout()
      }, timeoutMs)
    },
    clear() {
      seq += 1
      if (timer !== null) {
        clearTimer(timer)
        timer = null
      }
    },
  }
}
