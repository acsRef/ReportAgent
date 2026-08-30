import { describe, expect, it } from 'vitest'
import {
  ADJUST_STAGES,
  CONFIRM_STAGES,
  liveDetailFromEntry,
  progressPercent,
  stageFromTrace,
  stagePrefix,
} from '../progressModel'

describe('progressPercent', () => {
  it('follows the prototype pacing', () => {
    expect(progressPercent(0)).toBe(18)
    expect(progressPercent(1)).toBe(43)
    expect(progressPercent(2)).toBe(68)
    expect(progressPercent(3)).toBe(93)
  })
  it('caps at 99 until the real report event arrives', () => {
    expect(progressPercent(4)).toBe(99)
    expect(progressPercent(9)).toBe(99)
  })
})

describe('stage labels', () => {
  it('confirm stages are the exact prototype labels', () => {
    expect([...CONFIRM_STAGES]).toEqual([
      '需求已确认',
      '准备分析数据',
      '执行分析查询',
      '组织分析报告',
    ])
  })
  it('adjust stages are the exact prototype labels', () => {
    expect([...ADJUST_STAGES]).toEqual([
      '保留当前版本',
      '理解调整要求',
      '更新分析结果',
      '生成新版本',
    ])
  })
})

describe('stagePrefix', () => {
  it('done / active / pending matrix', () => {
    expect(stagePrefix(0, 2)).toBe('✓ ')
    expect(stagePrefix(1, 2)).toBe('✓ ')
    expect(stagePrefix(2, 2)).toBe('◌ ')
    expect(stagePrefix(3, 2)).toBe('○ ')
  })
})

describe('stageFromTrace (P11 真信号)', () => {
  it('maps kind to confirm stage', () => {
    expect(stageFromTrace('tool', 'running', 1)).toBe(1)
    expect(stageFromTrace('sql', 'running', 1)).toBe(2)
    expect(stageFromTrace('repair', 'success', 2)).toBe(2)
    expect(stageFromTrace('report', 'running', 2)).toBe(3)
  })
  it('monotonic — 永不回退；error 不打断进度', () => {
    expect(stageFromTrace('report', 'success', 3)).toBe(3)
    expect(stageFromTrace('sql', 'error', 2)).toBe(2)
  })
  it('未知 kind 保持当前 stage', () => {
    expect(stageFromTrace(undefined, 'running', 1)).toBe(1)
  })
})

describe('liveDetailFromEntry', () => {
  it('running → 正在…；success → 完成；error → 无文案', () => {
    expect(liveDetailFromEntry({ nodeName: '生成 SQL', status: 'running' } as any)).toBe('正在生成 SQL…')
    expect(liveDetailFromEntry({ nodeName: '执行查询', status: 'success' } as any)).toBe('执行查询 完成')
    expect(liveDetailFromEntry({ nodeName: 'x', status: 'error' } as any)).toBeUndefined()
  })
})
