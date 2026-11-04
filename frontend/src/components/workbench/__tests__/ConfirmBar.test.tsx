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
  extra: {
    changeNote?: string | null
    busy?: boolean
    trace?: React.ReactNode
    expanded?: boolean
    details?: React.ReactNode
  } = {},
) {
  const onConfirm = vi.fn()
  const onToggleDetails = vi.fn()
  const onSupplement = vi.fn()
  render(
    <ConfirmBar
      card={card}
      changeNote={extra.changeNote ?? null}
      onConfirm={onConfirm}
      onSupplement={onSupplement}
      onToggleDetails={onToggleDetails}
      expanded={extra.expanded ?? false}
      busy={extra.busy ?? false}
      trace={extra.trace}
      details={extra.details}
    />,
  )
  return { onConfirm, onToggleDetails, onSupplement }
}

describe('ConfirmBar — 一条助手消息做确认（2026-11-04 返工）', () => {
  it('先说「我按下面这些执行」，再列参数小结', () => {
    renderBar(makeCard())

    expect(screen.getByText('需求清楚了，我按下面这些执行：')).toBeTruthy()
    for (const text of ['2024年', '按周', '净销售额', '华东', '趋势分析']) {
      expect(screen.getByText(text)).toBeTruthy()
    }
  })

  it('「确认并生成报告」触发 onConfirm，「需求详情」触发 onToggleDetails', () => {
    const { onConfirm, onToggleDetails } = renderBar(makeCard())

    fireEvent.click(screen.getByText('确认并生成报告'))
    fireEvent.click(screen.getByText('需求详情'))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onToggleDetails).toHaveBeenCalledTimes(1)
  })

  // 2026-11-04（plan requirement-details-inline）：展开是**就地**的——
  // 确认按钮必须还在原位，详情出现在同一条气泡里，不是另起一张大卡。
  it('展开详情：确认按钮仍在原位，详情内联在同一个气泡里', () => {
    renderBar(makeCard(), {
      expanded: true,
      details: <div data-testid="brief">核心指标</div>,
    })

    expect(screen.getByTestId('brief')).toBeTruthy()
    expect(screen.getByText('确认并生成报告')).toBeTruthy()
    expect(screen.getByText('收起详情 ↑')).toBeTruthy()
    expect(screen.queryByText('需求详情')).toBeNull()
    // 只有一个助手头像 = 仍然是一条消息，没有另起一块
    expect(screen.getAllByText('A')).toHaveLength(1)
  })

  it('收起状态下不渲染详情内容', () => {
    renderBar(makeCard(), { details: <div data-testid="brief">核心指标</div> })

    expect(screen.queryByTestId('brief')).toBeNull()
  })

  // 2026-11-04 用户质问：「难道我就只能确认并生成报告吗 没有补充吗？」
  it('确认条必须给出「补充或修改」这个可见的第二动作', () => {
    const { onSupplement } = renderBar(makeCard())

    fireEvent.click(screen.getByText('补充或修改'))

    expect(onSupplement).toHaveBeenCalledTimes(1)
  })

  it('busy 时「补充或修改」也禁用（PATCH/确认进行中不该再开新补轮）', () => {
    const { onSupplement } = renderBar(makeCard(), { busy: true })

    fireEvent.click(screen.getByText('补充或修改'))

    expect(onSupplement).not.toHaveBeenCalled()
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
