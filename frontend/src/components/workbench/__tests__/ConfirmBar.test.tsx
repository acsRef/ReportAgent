import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConfirmBar from '../ConfirmBar'
import type { RequirementCard } from '../../../types/requirement'

function makeCard(overrides: Partial<RequirementCard> = {}): RequirementCard {
  return {
    id: 'draft-1',
    version: 1,
    status: 'complete',
    summary: '2024年各区域净销售额',
    target_metrics: ['净销售额'],
    time_range: '2024年',
    granularity: 'week',
    detail_level: null,
    scope: ['华东'],
    dimensions: ['时间'],
    analysis_methods: ['trend_analysis'],
    expected_blocks: [],
    missing_fields: [],
    assumptions: [],
    confidence: 0.9,
    confirmed_at: null,
    ...overrides,
  }
}

function renderBar(card: RequirementCard, extra: { changeNote?: string | null; busy?: boolean } = {}) {
  const onConfirm = vi.fn()
  const onExpand = vi.fn()
  render(
    <ConfirmBar
      card={card}
      changeNote={extra.changeNote ?? null}
      onConfirm={onConfirm}
      onExpand={onExpand}
      busy={extra.busy ?? false}
    />,
  )
  return { onConfirm, onExpand }
}

describe('ConfirmBar — 槽位问完后的紧凑确认条', () => {
  it('一行摘要显示已确认的时间/粒度/口径/范围/方法', () => {
    renderBar(makeCard())

    expect(screen.getByText('需求已明确')).toBeTruthy()
    for (const text of ['2024年', '按周', '净销售额', '华东', '趋势']) {
      expect(screen.getByText(text)).toBeTruthy()
    }
  })

  it('「确认并生成报告」触发 onConfirm，「展开详情」触发 onExpand', () => {
    const { onConfirm, onExpand } = renderBar(makeCard())

    fireEvent.click(screen.getByText('确认并生成报告'))
    fireEvent.click(screen.getByText('展开详情'))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onExpand).toHaveBeenCalledTimes(1)
  })

  it('补充轮的变化说明挂在确认条上', () => {
    renderBar(makeCard(), { changeNote: '数据范围 → 华东' })

    expect(screen.getByText('↻ 数据范围 → 华东')).toBeTruthy()
  })

  it('busy 时确认按钮禁用（避免重复 PATCH/confirm）', () => {
    const { onConfirm } = renderBar(makeCard(), { busy: true })

    fireEvent.click(screen.getByText('确认并生成报告'))

    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('没有任何可摘要字段时给出兜底文案', () => {
    renderBar(makeCard({
      time_range: null,
      granularity: null,
      target_metrics: [],
      scope: [],
      analysis_methods: [],
    }))

    expect(screen.getByText('按你的问题直接分析')).toBeTruthy()
  })
})
