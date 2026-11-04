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
    granularity: null,
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

  it('readOnly 后卡片不可编辑：没有确认/修改按钮，并提示已锁定', () => {
    const card = makeCard({ status: 'complete' })
    render(
      <ToastProvider>
        <RequirementCardView card={card} onChange={() => {}} onConfirm={() => {}} readOnly />
      </ToastProvider>,
    )
    expect(screen.queryByText('确认并生成报告')).toBeNull()
    expect(screen.queryByText('补充完成，查看确认')).toBeNull()
    expect(screen.queryByText('修改需求')).toBeNull()
    expect(screen.getByText(/需求已确认并锁定/)).toBeTruthy()
  })
})

// 2026-11-03（plan 2026-11-03-anomaly-granularity）：粒度此前只活在卡体字段里，
// 界面上看不到——用户不知道「异常类默认按周」，也不知道为什么结果是 53 行。
describe('RequirementCardView — 时间粒度 chip', () => {
  it('按周：出现「按周」chip，且紧跟时间范围', () => {
    const { container } = renderCard({
      card: makeCard({ status: 'complete', time_range: '2024年', granularity: 'week' }),
    })
    expect(screen.getByText('按周')).toBeTruthy()
    const chips = Array.from(container.querySelectorAll('.wb-chips .wb-chip')).map(
      (el) => el.textContent,
    )
    expect(chips.indexOf('按周')).toBe(chips.indexOf('2024年') + 1)
  })

  it('按日 / 按月 / 按季度 / 按年 都有对应中文标签', () => {
    for (const [value, label] of [
      ['day', '按日'],
      ['month', '按月'],
      ['quarter', '按季度'],
      ['year', '按年'],
    ] as const) {
      const { unmount } = renderCard({
        card: makeCard({ status: 'complete', granularity: value }),
      })
      expect(screen.getByText(label)).toBeTruthy()
      unmount()
    }
  })

  it('无粒度时不多显示 chip（旧会话卡片兼容）', () => {
    renderCard({ card: makeCard({ status: 'complete', granularity: null }) })
    for (const label of ['按日', '按周', '按月', '按季度', '按年']) {
      expect(screen.queryByText(label)).toBeNull()
    }
  })

  it('只读卡片（已确认/执行中）同样显示粒度', () => {
    render(
      <ToastProvider>
        <RequirementCardView
          card={makeCard({ status: 'complete', granularity: 'week' })}
          onChange={() => {}}
          onConfirm={() => {}}
          readOnly
        />
      </ToastProvider>,
    )
    expect(screen.getByText('按周')).toBeTruthy()
  })

  it('建议分析方法显示中文标签，不露 group_compare 这种 id', () => {
    renderCard({
      card: makeCard({
        status: 'complete',
        missing_fields: [],
        assumptions: [],
        analysis_methods: ['group_compare', 'anomaly_detection'],
      }),
    })

    expect(screen.getByText('对比/排名')).toBeTruthy()
    expect(screen.getByText('异常检测')).toBeTruthy()
    expect(screen.queryByText('group_compare')).toBeNull()
  })
})

describe('RequirementCardView — inline 详情形态（2026-11-04 plan requirement-details-inline）', () => {
  const inlineCard = () => makeCard({
    status: 'complete',
    missing_fields: [],
    assumptions: [],
    time_range: '2024年',
    granularity: 'week',
    scope: ['华东'],
    expected_blocks: [],
  })

  function renderInline(overrides: Partial<RequirementCard> = {}) {
    const { container } = render(
      <ToastProvider>
        <RequirementCardView
          card={{ ...inlineCard(), ...overrides }}
          onChange={() => {}}
          onConfirm={() => {}}
          readOnly
          variant="inline"
        />
      </ToastProvider>,
    )
    return container
  }

  it('没有卡片外壳、没有 brief 标题、没有自己的确认按钮', () => {
    const container = renderInline()
    expect(container.querySelector('.wb-requirement-card.inline')).not.toBeNull()
    expect(screen.queryByText('AGENT REQUIREMENT BRIEF')).toBeNull()
    expect(screen.queryByText('需求解析与执行确认')).toBeNull()
    expect(screen.queryByText('确认并生成报告')).toBeNull()
    expect(screen.queryByText('修改需求')).toBeNull()
    expect(screen.getByText('需求详情')).toBeTruthy()
  })

  it('参数仍然读得到：摘要 + 指标 / 时间范围 / 方法', () => {
    renderInline()
    expect(screen.getByText('分析 2024 年华东区域的销售表现')).toBeTruthy()
    expect(screen.getByText('销售额')).toBeTruthy()
    expect(screen.getByText('2024年')).toBeTruthy()
    expect(screen.getByText('按周')).toBeTruthy()
    expect(screen.getByText('华东')).toBeTruthy()
    expect(screen.getByText('月度趋势')).toBeTruthy()
  })

  // 截图里的另一处难看：分组只有标题、下面一片空白
  it('空分组不再渲染空壳（预计报告内容为空时不出现）', () => {
    renderInline()
    expect(screen.queryByText('预计报告内容')).toBeNull()
  })

  it('缺失项编辑区不进内联详情（问答在对话里问）', () => {
    const container = renderInline({ status: 'missing', missing_fields: [{
      key: 'time_range',
      label: '时间范围',
      kind: 'single',
      options: [{ label: '本月', value: '本月' }],
      selected_value: null,
    }] })
    expect(container.querySelector('.wb-missing-zone')).toBeNull()
  })
})
