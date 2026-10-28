import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RequirementCardView from '../RequirementCardView'
import { ToastProvider } from '../../atelier/Toast'
import type { RequirementCard } from '../../../types/requirement'

function makeCard(overrides: Partial<RequirementCard> = {}): RequirementCard {
  return {
    id: 'draft-1',
    version: 2,
    status: 'missing',
    summary: '分析 2024 年华东区域的销售表现',
    target_metrics: ['销售额', '毛利额'],
    time_range: null,
    scope: [],
    dimensions: ['时间', '区域'],
    analysis_methods: ['月度趋势', '区域贡献'],
    expected_blocks: ['核心结论', '关键指标', '趋势图', '数据明细'],
    missing_fields: [
      {
        key: 'time_range',
        label: '时间范围',
        kind: 'single',
        options: [
          { label: '本月', value: '本月' },
          { label: '今年', value: '今年' },
        ],
        selected_value: null,
      },
    ],
    assumptions: [
      { key: 'a1', text: '按月环比，并对异常月份做原因解释', accepted: null, alternatives: [] },
    ],
    confidence: 0.8,
    confirmed_at: null,
    ...overrides,
  }
}

function renderCard(props: Partial<React.ComponentProps<typeof RequirementCardView>> = {}) {
  const onChange = vi.fn()
  const onConfirm = vi.fn()
  const utils = render(
    <ToastProvider>
      <RequirementCardView
        card={makeCard()}
        onChange={onChange}
        onConfirm={onConfirm}
        {...props}
      />
    </ToastProvider>,
  )
  return { onChange, onConfirm, ...utils }
}

/** Stateful harness: the card is controlled, so interactive tests must
 *  write onChange results back into the rendered props. */
function renderStateful(initial: RequirementCard) {
  const onChange = vi.fn()
  const onConfirm = vi.fn()
  function Harness() {
    const [card, setCard] = useState(initial)
    return (
      <ToastProvider>
        <RequirementCardView
          card={card}
          onChange={(next) => {
            onChange(next)
            setCard(next)
          }}
          onConfirm={onConfirm}
        />
      </ToastProvider>
    )
  }
  const utils = render(<Harness />)
  return { onChange, onConfirm, ...utils }
}

describe('RequirementCardView — prototype structure', () => {
  it('missing card: amber accent, kicker, 需要补充 N 项 pill', () => {
    const { container } = renderCard()
    expect(container.querySelector('.wb-requirement-card.missing')).not.toBeNull()
    expect(screen.getByText('AGENT REQUIREMENT BRIEF')).toBeTruthy()
    expect(screen.getByText('需求解析与执行确认')).toBeTruthy()
    expect(screen.getByText('需要补充 1 项')).toBeTruthy()
    expect(screen.getByText('选项由后端根据当前问题返回')).toBeTruthy()
  })

  it('renders 时间待补充 placeholder but shows 未限定 for empty scope (not 待补充)', () => {
    renderCard()
    expect(screen.getByText('核心指标')).toBeTruthy()
    expect(screen.getByText('销售额')).toBeTruthy()
    expect(screen.getByText('时间待补充')).toBeTruthy()
    // scope 为空且不在 missing_fields 里 → 未限定（默认全部），不是"待补充"
    expect(screen.getByText('未限定（全部区域）')).toBeTruthy()
    expect(screen.queryByText('范围待补充')).toBeNull()
    expect(screen.getByText('预计报告内容')).toBeTruthy()
  })

  it('shows 范围待补充 only when scope is a real missing field', () => {
    renderCard({
      card: makeCard({
        missing_fields: [
          { key: 'scope', label: '范围', kind: 'multiple', options: [], selected_value: null },
        ],
      }),
    })
    expect(screen.getByText('范围待补充')).toBeTruthy()
    expect(screen.queryByText('未限定（全部区域）')).toBeNull()
  })

  it('one-click 口径 alternative: accepts the assumption and records choice into text', () => {
    const card = makeCard({
      assumptions: [
        {
          key: 'field_meaning:销量',
          text: '销量可能指订单数量或商品件数，请确认',
          accepted: null,
          alternatives: [
            { label: '订单数（按订单计数）', value: 'COUNT(*)' },
            { label: '商品件数（按销量求和）', value: 'SUM(quantity)' },
          ],
        },
      ],
    })
    const { onChange } = renderCard({ card })

    fireEvent.click(screen.getByText('选：商品件数（按销量求和）'))

    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.calls[0][0] as RequirementCard
    expect(next.assumptions[0].accepted).toBe(true)
    expect(next.assumptions[0].text).toBe(
      '销量可能指订单数量或商品件数，请确认 → 已选：商品件数（按销量求和）',
    )
  })

  it('review button disabled until field selected AND assumption resolved', () => {
    renderStateful(makeCard())
    const review = screen.getByText('补充完成，查看确认') as HTMLButtonElement
    expect(review.disabled).toBe(true)
    // select the pill option — still disabled (assumption unresolved)
    fireEvent.click(screen.getByText('今年'))
    expect((screen.getByText('补充完成，查看确认') as HTMLButtonElement).disabled).toBe(true)
    // accept the assumption — now enabled
    fireEvent.click(screen.getByText('接受'))
    expect((screen.getByText('补充完成，查看确认') as HTMLButtonElement).disabled).toBe(false)
  })

  it('review click promotes the card to complete via onChange', () => {
    const { onChange } = renderCard({
      card: makeCard({
        missing_fields: [
          { key: 'time_range', label: '时间范围', kind: 'single', options: [], selected_value: '今年' },
        ],
        assumptions: [{ key: 'a1', text: 'x', accepted: true, alternatives: [] }],
      }),
    })
    fireEvent.click(screen.getByText('补充完成，查看确认'))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'complete' }))
  })

  it('complete card: 确认并生成报告 triggers onConfirm; 修改需求 demotes', () => {
    const { onChange, onConfirm } = renderCard({
      card: makeCard({ status: 'complete', missing_fields: [], assumptions: [] }),
    })
    expect(screen.getByText('信息完整 · 待确认')).toBeTruthy()
    fireEvent.click(screen.getByText('确认并生成报告'))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('修改需求'))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'missing' }))
  })

  it('locked card: no footer actions, ✓ 已确认 pill, never a spinner', () => {
    const { container } = renderCard({
      card: makeCard({ status: 'locked', missing_fields: [], assumptions: [], confirmed_at: '2026-07-27' }),
    })
    expect(container.querySelector('.wb-requirement-card.locked')).not.toBeNull()
    expect(screen.getByText('✓ 已确认')).toBeTruthy()
    expect(screen.queryByText('确认并生成报告')).toBeNull()
    expect(screen.queryByText('补充完成，查看确认')).toBeNull()
    expect(container.querySelector('[aria-busy]')).toBeNull()
  })

  it('accepting an assumption flips the mini-btn to ✓ 已接受', () => {
    renderStateful(makeCard())
    fireEvent.click(screen.getByText('接受'))
    expect(screen.getByText('✓ 已接受')).toBeTruthy()
  })
})
