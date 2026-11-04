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

function renderBar(
  card: RequirementCard,
  extra: { changeNote?: string | null; busy?: boolean; trace?: React.ReactNode } = {},
) {
  const onConfirm = vi.fn()
  const onExpand = vi.fn()
  render(
    <ConfirmBar
      card={card}
      changeNote={extra.changeNote ?? null}
      onConfirm={onConfirm}
      onExpand={onExpand}
      busy={extra.busy ?? false}
      trace={extra.trace}
    />,
  )
  return { onConfirm, onExpand }
}

describe('ConfirmBar — 一条助手消息做确认（2026-11-04 返工）', () => {
  it('先说「我按下面这些执行」，再列参数小结', () => {
    renderBar(makeCard())

    expect(screen.getByText('需求清楚了，我按下面这些执行：')).toBeTruthy()
    for (const text of ['2024年', '按周', '净销售额', '华东', '趋势分析']) {
      expect(screen.getByText(text)).toBeTruthy()
    }
  })

  it('「确认并生成报告」触发 onConfirm，「需求详情」触发 onExpand', () => {
    const { onConfirm, onExpand } = renderBar(makeCard())

    fireEvent.click(screen.getByText('确认并生成报告'))
    fireEvent.click(screen.getByText('需求详情'))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onExpand).toHaveBeenCalledTimes(1)
  })

  it('补充轮的变更说明直接画进这句话里（不再是另一块提示条）', () => {
    renderBar(makeCard(), { changeNote: '数据范围 → 华东' })

    expect(screen.getByText('已按你的补充更新：数据范围 → 华东')).toBeTruthy()
  })

  it('busy 时确认按钮禁用（避免重复 PATCH/confirm）', () => {
    const { onConfirm } = renderBar(makeCard(), { busy: true })

    fireEvent.click(screen.getByText('确认并生成报告'))

    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('没有可摘要字段时只出这句话与按钮', () => {
    renderBar(makeCard({
      time_range: null,
      granularity: null,
      target_metrics: [],
      scope: [],
      analysis_methods: [],
    }))

    expect(screen.getByText('需求清楚了，我按下面这些执行：')).toBeTruthy()
    expect(screen.getByText('确认并生成报告')).toBeTruthy()
  })

  it('思考过程与确认在同一条消息里（trace 插槽）', () => {
    renderBar(makeCard(), { trace: <span>思考了 2 步 · 3.0s</span> })

    expect(screen.getByText('思考了 2 步 · 3.0s')).toBeTruthy()
    expect(screen.getAllByText('A')).toHaveLength(1)
  })
})
