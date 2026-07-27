import { describe, expect, it } from 'vitest'
import { isDraftReadyForReview, type RequirementCard } from '../requirement'

function makeCard(overrides: Partial<RequirementCard> = {}): RequirementCard {
  return {
    id: 'draft-1',
    version: 1,
    status: 'missing',
    summary: '分析 2024 年华东销售',
    target_metrics: ['销售额'],
    time_range: null,
    scope: [],
    dimensions: ['时间', '区域'],
    analysis_methods: ['trend_analysis'],
    expected_blocks: ['核心结论', '趋势图', '数据明细'],
    missing_fields: [],
    assumptions: [],
    confidence: 0.8,
    confirmed_at: null,
    ...overrides,
  }
}

describe('isDraftReadyForReview', () => {
  it('no missing fields and no assumptions → ready', () => {
    expect(isDraftReadyForReview(makeCard())).toBe(true)
  })

  it('unselected single field → not ready', () => {
    const card = makeCard({
      missing_fields: [
        { key: 'time_range', label: '时间范围', kind: 'single', options: [{ label: '今年', value: '今年' }], selected_value: null },
      ],
    })
    expect(isDraftReadyForReview(card)).toBe(false)
  })

  it('selected single field → ready', () => {
    const card = makeCard({
      missing_fields: [
        { key: 'time_range', label: '时间范围', kind: 'single', options: [], selected_value: '今年' },
      ],
    })
    expect(isDraftReadyForReview(card)).toBe(true)
  })

  it('empty multi-select counts as unfilled', () => {
    const card = makeCard({
      missing_fields: [
        { key: 'scope', label: '范围', kind: 'multiple', options: [], selected_value: [] },
      ],
    })
    expect(isDraftReadyForReview(card)).toBe(false)
  })

  it('unresolved assumption → not ready', () => {
    const card = makeCard({
      assumptions: [{ key: 'a1', text: '默认月度', accepted: null, alternatives: [] }],
    })
    expect(isDraftReadyForReview(card)).toBe(false)
  })

  it('rejected assumption still counts as resolved', () => {
    const card = makeCard({
      assumptions: [{ key: 'a1', text: '默认月度', accepted: false, alternatives: [] }],
    })
    expect(isDraftReadyForReview(card)).toBe(true)
  })
})
