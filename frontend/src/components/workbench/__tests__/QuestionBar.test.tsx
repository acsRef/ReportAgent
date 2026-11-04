import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import QuestionBar from '../QuestionBar'
import type { RequirementMissingField } from '../../../types/requirement'

function makeField(overrides: Partial<RequirementMissingField> = {}): RequirementMissingField {
  return {
    key: 'metric',
    label: '用哪个口径？',
    kind: 'single',
    state: 'ambiguous',
    options: [
      { label: '销售额', value: 'metric:销售额', hint: '2.63 亿元 · 订单金额口径，未扣退款' },
      { label: '净销售额', value: 'metric:净销售额', hint: '2.51 亿元 · 销售额减去退款' },
    ],
    selected_value: null,
    ...overrides,
  }
}

function renderBar(field: RequirementMissingField, disabled = false) {
  const onPick = vi.fn()
  const onSupplement = vi.fn()
  render(
    <QuestionBar
      field={field}
      onPick={onPick}
      onSupplement={onSupplement}
      disabled={disabled}
    />,
  )
  return { onPick, onSupplement }
}

describe('QuestionBar — 一次只问一个槽位', () => {
  it('渲染问句与选项，选项上带口径预览数值', () => {
    renderBar(makeField())

    expect(screen.getByText('用哪个口径？')).toBeTruthy()
    expect(screen.getByText('需要你定口径')).toBeTruthy()
    expect(screen.getByText('销售额')).toBeTruthy()
    expect(screen.getByText('2.63 亿元 · 订单金额口径，未扣退款')).toBeTruthy()
    expect(screen.getByText('2.51 亿元 · 销售额减去退款')).toBeTruthy()
  })

  it('点选项 → onPick(槽位 key, 选项 value)', () => {
    const { onPick } = renderBar(makeField())

    fireEvent.click(screen.getByText('净销售额'))

    expect(onPick).toHaveBeenCalledWith('metric', 'metric:净销售额')
  })

  it('点「都不是，我补充说明」→ onSupplement', () => {
    const { onSupplement } = renderBar(makeField())

    fireEvent.click(screen.getByText('都不是，我补充说明'))

    expect(onSupplement).toHaveBeenCalledTimes(1)
  })

  it('PATCH 进行中（disabled）时点击不触发 onPick', () => {
    const { onPick } = renderBar(makeField(), true)

    fireEvent.click(screen.getByText('销售额'))

    expect(onPick).not.toHaveBeenCalled()
  })

  it('缺失（非歧义）槽位用另一句 kicker', () => {
    renderBar(makeField({ state: 'missing', label: '时间范围' }))

    expect(screen.getByText('还需要一个信息')).toBeTruthy()
  })

  it('多选槽位给出可多选提示', () => {
    renderBar(makeField({ key: 'scope', kind: 'multiple', label: '范围' }))

    expect(screen.getByText(/可多选/)).toBeTruthy()
  })
})
