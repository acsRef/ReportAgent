import { describe, expect, it } from 'vitest'
import { ADJUST_STAGES, CONFIRM_STAGES, progressPercent, stagePrefix } from '../progressModel'

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
