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

function renderBar(field: RequirementMissingField, disabled = false, trace?: React.ReactNode) {
  const onPick = vi.fn()
  const onSupplement = vi.fn()
  render(
    <QuestionBar
      field={field}
      onPick={onPick}
      onSupplement={onSupplement}
      disabled={disabled}
      trace={trace}
    />,
  )
  return { onPick, onSupplement }
}

describe('QuestionBar — 一条助手消息里问一个问题（2026-11-04 返工）', () => {
  it('用「先确认一下口径」引出问句，选项带口径预览数值', () => {
    renderBar(makeField())

    expect(screen.getByText(/先确认一下口径/)).toBeTruthy()
    expect(screen.getByText(/用哪个口径？/)).toBeTruthy()
    expect(screen.getByText('销售额')).toBeTruthy()
    expect(screen.getByText('2.63 亿元 · 订单金额口径，未扣退款')).toBeTruthy()
  })

  it('点选项 → onPick(槽位 key, 选项 value)', () => {
    const { onPick } = renderBar(makeField())

    fireEvent.click(screen.getByText('净销售额'))

    expect(onPick).toHaveBeenCalledWith('metric', 'metric:净销售额')
  })

  it('「都不是，我补充说明」→ onSupplement', () => {
    const { onSupplement } = renderBar(makeField())

    fireEvent.click(screen.getByText('都不是，我补充说明'))

    expect(onSupplement).toHaveBeenCalledTimes(1)
  })

  it('缺失（非歧义）时换一句话', () => {
    renderBar(makeField({ state: 'missing', label: '时间范围' }))

    expect(screen.getByText(/还需要你补充一点/)).toBeTruthy()
  })

  it('PATCH 进行中（disabled）时点击不触发 onPick', () => {
    const { onPick } = renderBar(makeField(), true)

    fireEvent.click(screen.getByText('销售额'))

    expect(onPick).not.toHaveBeenCalled()
  })

  it('多选槽位给出可多选提示', () => {
    renderBar(makeField({ key: 'scope', kind: 'multiple', label: '范围' }))

    expect(screen.getByText('可多选')).toBeTruthy()
  })

  it('思考过程与提问在同一条消息里（trace 插槽）', () => {
    renderBar(makeField(), false, <span>思考了 6 步</span>)

    expect(screen.getByText('思考了 6 步')).toBeTruthy()
    // 只有一条助手消息（一个头像）
    expect(screen.getAllByText('A')).toHaveLength(1)
  })
})
