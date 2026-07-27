/** Session list presentation helpers — prototype texts, injectable now. */
import type { AnalysisPhase } from '../../types/analysis'

export interface StatusPill {
  text: string
  cls: '' | 'confirm' | 'running' | 'done' | 'error'
}

export function statusPill(phase: AnalysisPhase): StatusPill {
  switch (phase) {
    case 'idle':
      return { text: '未开始', cls: '' }
    case 'parsing':
      return { text: '正在解析', cls: 'running' }
    case 'awaiting_missing':
      return { text: '等待补充', cls: 'confirm' }
    case 'awaiting_confirm':
      return { text: '等待确认', cls: 'confirm' }
    case 'generating':
      return { text: '生成中', cls: 'running' }
    case 'adjusting':
      return { text: '生成新版本', cls: 'running' }
    case 'report_ready':
      return { text: '已完成', cls: 'done' }
    case 'error':
      return { text: '生成失败', cls: 'error' }
  }
}

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** 刚刚 / N 分钟前 / N 小时前 / 昨天 / N 天前 — prototype relative times. */
export function relativeTime(iso: string | null | undefined, now: number): string {
  if (!iso) return ''
  const parsed = Date.parse(iso)
  if (!Number.isFinite(parsed)) return ''
  const delta = now - parsed
  if (delta <= 5 * MINUTE) return '刚刚'
  if (delta < HOUR) return `${Math.max(1, Math.floor(delta / MINUTE))} 分钟前`
  if (delta < DAY) return `${Math.floor(delta / HOUR)} 小时前`
  if (delta < 2 * DAY) return '昨天'
  return `${Math.floor(delta / DAY)} 天前`
}
