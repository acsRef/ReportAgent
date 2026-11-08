import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConfirmBar from '../ConfirmBar'
import { describeRequirement } from '../slotQuestion'
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

function renderBar(card: RequirementCard, extra: { busy?: boolean; trace?: React.ReactNode } = {}) {
  const onConfirm = vi.fn()
  const onSupplement = vi.fn()
  render(
    <ConfirmBar
      card={card}
      onConfirm={onConfirm}
      onSupplement={onSupplement}
      busy={extra.busy ?? false}
      trace={extra.trace}
    />,
  )
  return { onConfirm, onSupplement }
}

describe('ConfirmBar — 确认就是一句话（2026-11-05 二次改造）', () => {
  // 用户原话：「不要卡片的话，直接改成对话的方式会不会好一些……直接从对话中来读取意图」
  it('把已确认的要素说成一句人话，不再有参数 chip / diff 回执', () => {
    renderBar(makeCard())

    expect(screen.getByText(/好，2024年华东的净销售额，按周，趋势分析/)).toBeTruthy()
    expect(screen.getByText('生成报告')).toBeTruthy()
    // 旧形态的痕迹都不该出现
    expect(screen.queryByText(/已按你的补充更新/)).toBeNull()
    expect(document.querySelectorAll('.wb-ask-chip')).toHaveLength(0)
    // 2026-11-05 修订 A4：「需求详情」是**按需**视图——默认收起，不占主流程
    expect(document.querySelector('.wb-ask-details')).toBeNull()
    expect(screen.queryByText('核心指标')).toBeNull()
  })

  // 用户要求「一条消息就是一条消息」：详情在**这条气泡里**原地展开，按钮不动。
  it('点「需求详情」就地展开参数，「收起详情」再关掉', () => {
    const { onConfirm } = renderBar(makeCard())

    fireEvent.click(screen.getByText('需求详情'))
    expect(document.querySelector('.wb-ask-details')).toBeTruthy()
    expect(screen.getByText('核心指标')).toBeTruthy()

    fireEvent.click(screen.getByText('收起详情'))
    expect(document.querySelector('.wb-ask-details')).toBeNull()

    // 展开/收起不触发确认
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('「生成报告」触发 onConfirm，「补充或修改」触发 onSupplement', () => {
    const { onConfirm, onSupplement } = renderBar(makeCard())

    fireEvent.click(screen.getByText('生成报告'))
    fireEvent.click(screen.getByText('补充或修改'))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onSupplement).toHaveBeenCalledTimes(1)
  })

  // 2026-11-08 真机：卡上还有待确认项时，这句「要我现在生成报告吗？」是假话。
  it('还有待确认项时不说「要我现在生成报告吗」，并且禁用生成', () => {
    const card = makeCard({
      status: 'missing',
      missing_fields: [
        {
          key: 'unsupported',
          label: '指标未注册：销售',
          kind: 'single',
          options: [],
          selected_value: null,
        },
      ],
    })
    const { onConfirm } = renderBar(card)

    expect(screen.getByText(/还差 1 项/)).toBeTruthy()
    expect(screen.getByText(/指标未注册：销售/)).toBeTruthy()
    expect(screen.queryByText(/要我现在生成报告吗/)).toBeNull()

    const confirmBtn = screen.getByText('生成报告') as HTMLButtonElement
    expect(confirmBtn.disabled).toBe(true)
    fireEvent.click(confirmBtn)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('没有待确认项时仍是那句话，按钮可点', () => {
    renderBar(makeCard())

    expect(screen.getByText(/要我现在生成报告吗/)).toBeTruthy()
    expect((screen.getByText('生成报告') as HTMLButtonElement).disabled).toBe(false)
  })

  it('busy 时两个动作都禁用（避免重复 PATCH/confirm）', () => {    const { onConfirm, onSupplement } = renderBar(makeCard(), { busy: true })

    fireEvent.click(screen.getByText('生成报告'))
    fireEvent.click(screen.getByText('补充或修改'))

    expect(onConfirm).not.toHaveBeenCalled()
    expect(onSupplement).not.toHaveBeenCalled()
  })

  it('思考过程与这句话在同一条消息里（trace 插槽）', () => {
    renderBar(makeCard(), { trace: <span>思考了 2 步 · 3.0s</span> })

    expect(screen.getByText('思考了 2 步 · 3.0s')).toBeTruthy()
    expect(screen.getAllByText('A')).toHaveLength(1)
  })
})

describe('describeRequirement — 参数 → 一句话', () => {
  it('时间 + 范围 + 口径 + 粒度 + 方法', () => {
    expect(describeRequirement(makeCard())).toBe('2024年华东的净销售额，按周，趋势分析')
  })

  it('空卡不崩', () => {
    expect(describeRequirement(null)).toBe('')
  })
})
