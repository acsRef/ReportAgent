/**
 * Progress card model — stage labels and pacing from the prototype.
 * The backend /confirm stream only carries phase events, so stages 1-3
 * advance on a 650ms timer capped at 99%; real signals (report/error
 * events) snap the card to its final state in WorkbenchPage.
 */

export const CONFIRM_STAGES = [
  '需求已确认',
  '准备分析数据',
  '执行分析查询',
  '组织分析报告',
] as const

export const ADJUST_STAGES = [
  '保留当前版本',
  '理解调整要求',
  '更新分析结果',
  '生成新版本',
] as const

export function progressPercent(stageIndex: number): number {
  return Math.min(99, 18 + stageIndex * 25)
}

/** ✓ done / ◌ active / ○ pending — prototype prefixes. */
export function stagePrefix(index: number, activeIndex: number): string {
  if (index < activeIndex) return '✓ '
  if (index === activeIndex) return '◌ '
  return '○ '
}
