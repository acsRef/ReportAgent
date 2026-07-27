import { describe, expect, it } from 'vitest'
import { relativeTime, statusPill } from '../sessionMeta'

describe('statusPill — exact prototype mappings', () => {
  it('idle', () => expect(statusPill('idle')).toEqual({ text: '未开始', cls: '' }))
  it('parsing', () => expect(statusPill('parsing')).toEqual({ text: '正在解析', cls: 'running' }))
  it('awaiting_missing', () =>
    expect(statusPill('awaiting_missing')).toEqual({ text: '等待补充', cls: 'confirm' }))
  it('awaiting_confirm', () =>
    expect(statusPill('awaiting_confirm')).toEqual({ text: '等待确认', cls: 'confirm' }))
  it('generating', () => expect(statusPill('generating')).toEqual({ text: '生成中', cls: 'running' }))
  it('adjusting', () =>
    expect(statusPill('adjusting')).toEqual({ text: '生成新版本', cls: 'running' }))
  it('report_ready', () =>
    expect(statusPill('report_ready')).toEqual({ text: '已完成', cls: 'done' }))
  it('error', () => expect(statusPill('error')).toEqual({ text: '生成失败', cls: 'error' }))
})

describe('relativeTime', () => {
  const NOW = Date.parse('2026-07-27T12:00:00Z')
  const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()

  it('≤5min → 刚刚', () => expect(relativeTime(iso(4 * 60_000), NOW)).toBe('刚刚'))
  it('32min → N 分钟前', () => expect(relativeTime(iso(32 * 60_000), NOW)).toBe('32 分钟前'))
  it('20h → N 小时前', () => expect(relativeTime(iso(20 * 3_600_000), NOW)).toBe('20 小时前'))
  it('30h → 昨天', () => expect(relativeTime(iso(30 * 3_600_000), NOW)).toBe('昨天'))
  it('3d → N 天前', () => expect(relativeTime(iso(3 * 86_400_000), NOW)).toBe('3 天前'))
  it('invalid/empty → empty string', () => {
    expect(relativeTime('', NOW)).toBe('')
    expect(relativeTime('not-a-date', NOW)).toBe('')
    expect(relativeTime(null, NOW)).toBe('')
  })
})
