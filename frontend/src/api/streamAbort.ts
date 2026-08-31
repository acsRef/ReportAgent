/**
 * 「活跃 SSE 流」单例 abort 控制器——P11 Review-1 P1-2 修复：
 * confirm / retry / adjust 三条路径写入同一 ref，「停止」按钮对其 abort。
 * 模块级单例替代 useRef（避免 ref 跨组件边界难以单测）。
 */

let active: AbortController | null = null

/** 写入新 controller；旧 controller 立即 abort（防止两条流并存）。 */
export function armStream(controller: AbortController): void {
  active?.abort()
  active = controller
}

/** abort 当前活跃流；返回是否真 abort 了任何东西。 */
export function abortStream(): boolean {
  if (!active) return false
  active.abort()
  active = null
  return true
}

/** 当前是否有活跃流（按钮 disabled 判断用）。 */
export function hasActiveStream(): boolean {
  return active !== null
}

/** 仅供测试用：清空 module state。 */
export function __resetStreamForTest(): void {
  active?.abort()
  active = null
}