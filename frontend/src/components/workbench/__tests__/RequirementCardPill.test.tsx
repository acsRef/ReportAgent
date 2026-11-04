import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import RequirementCardView from '../RequirementCardView'
import type { RequirementCard } from '../../../types/requirement'

function card(overrides: Partial<RequirementCard> = {}): RequirementCard {
  return {
    id: 'd1', version: 1, status: 'missing', summary: 'x',
    target_metrics: ['销售额'], time_range: '2024年', granularity: null,
    detail_level: null, scope: ['华东'], dimensions: ['时间'],
    analysis_methods: [], expected_blocks: [], missing_fields: [],
    assumptions: [], confidence: 0.8, confirmed_at: null, ...overrides,
  }
}

describe('RequirementCardView — 状态 pill 不再自相矛盾（2026-11-04）', () => {
  it('status=missing 但只有未表态假设 → 「待确认 N 项假设」', () => {
    render(
      <RequirementCardView
        card={card({ assumptions: [{ key: 'a1', text: '按订单金额口径', accepted: null, alternatives: [] }] })}
        onChange={() => {}} onConfirm={() => {}}
      />,
    )
    expect(screen.getByText('待确认 1 项假设')).toBeTruthy()
    expect(screen.queryByText('需要补充 0 项')).toBeNull()
  })

  it('真有缺失项时仍显示需要补充 N 项', () => {
    render(
      <RequirementCardView
        card={card({ missing_fields: [{ key: 'metric', label: '口径？', kind: 'single', options: [], selected_value: null }] })}
        onChange={() => {}} onConfirm={() => {}}
      />,
    )
    expect(screen.getByText('需要补充 1 项')).toBeTruthy()
  })
})
