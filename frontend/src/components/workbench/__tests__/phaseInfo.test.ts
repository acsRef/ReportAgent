import { describe, expect, it } from 'vitest'
import { PHASE_INFO, REPORT_SUGGESTIONS } from '../phaseInfo'

describe('PHASE_INFO — exact prototype triples', () => {
  it('idle', () =>
    expect(PHASE_INFO.idle).toEqual({
      name: '开始新分析',
      copy: '输入业务问题后，Agent 会先拆解需求，不会直接查询数据。',
      completeness: 0,
    }))
  it('parsing', () => expect(PHASE_INFO.parsing.completeness).toBe(28))
  it('awaiting_missing', () =>
    expect(PHASE_INFO.awaiting_missing).toEqual({
      name: '等待补充信息',
      copy: '后端判断当前需求还有关键信息未确定。',
      completeness: 55,
    }))
  it('awaiting_confirm', () => expect(PHASE_INFO.awaiting_confirm.name).toBe('等待最终确认'))
  it('generating', () => expect(PHASE_INFO.generating.name).toBe('正在生成报告'))
  it('adjusting', () => expect(PHASE_INFO.adjusting.copy).toContain('保留当前版本'))
  it('report_ready', () => expect(PHASE_INFO.report_ready.completeness).toBe(100))
  it('error offers retry without a new session', () =>
    expect(PHASE_INFO.error.copy).toBe('错误保留在当前会话，可直接重试。'))
  it('every phase has a triple', () => {
    expect(Object.keys(PHASE_INFO)).toHaveLength(8)
    for (const info of Object.values(PHASE_INFO)) {
      expect(info.name.length).toBeGreaterThan(0)
      expect(info.copy.length).toBeGreaterThan(0)
    }
  })
})

describe('REPORT_SUGGESTIONS', () => {
  it('exact prototype strings', () => {
    expect(REPORT_SUGGESTIONS).toEqual(['增加华南区域对比', '深入解释异常月份'])
  })
})
