/**
 * Progress card model — stage labels and pacing from the prototype.
 * The backend /confirm stream carries phase events; P11 起真实 progress
 * trace（kind×status）驱动 stage——阶段不再按 650ms 假定时器推进。
 */

import type { TimelineEntry, TraceProgressKind } from '../../types/report'

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

/** P11：trace kind → confirm stage（索引 1..3，对应 CONFIRM_STAGES）。 */
const KIND_STAGE: Record<TraceProgressKind, number> = {
  agent: 1, // 规划查询 / 执行 SQL 分析
  tool: 1, // 准备分析数据
  sql: 2, // 生成 / 校验 / 执行 SQL
  repair: 2, // 诊断修复
  report: 3, // 组织报告
}

/**
 * P11：由 progress trace 推导当前 stage。单调不减（进度不回退）；
 * error 不打断（终态由 error 事件负责）。未知 kind 保持现状。
 */
export function stageFromTrace(
  kind: TraceProgressKind | undefined,
  status: TimelineEntry['status'],
  current: number,
): number {
  if (status === 'error' || kind === undefined) return current
  return Math.max(current, KIND_STAGE[kind])
}

/** P11：live 当前步骤文案——running「正在X…」/ success「X 完成」，其余无。 */
export function liveDetailFromEntry(entry: Pick<TimelineEntry, 'nodeName' | 'status'>): string | undefined {
  if (entry.status === 'running') return `正在${entry.nodeName}…`
  if (entry.status === 'success') return `${entry.nodeName} 完成`
  return undefined
}
