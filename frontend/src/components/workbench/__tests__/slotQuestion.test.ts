import { describe, expect, it } from 'vitest'
import { confirmChips, hasPendingQuestion, pickQuestionField } from '../slotQuestion'
import type { RequirementCard, RequirementMissingField } from '../../../types/requirement'

function field(
  key: string,
  options: Array<[string, string]> = [['销售额', 'metric:销售额']],
  extra: Partial<RequirementMissingField> = {},
): RequirementMissingField {
  return {
    key: key as RequirementMissingField['key'],
    label: `${key}?`,
    kind: 'single',
    options: options.map(([label, value]) => ({ label, value })),
    selected_value: null,
    ...extra,
  }
}

function card(overrides: Partial<RequirementCard> = {}): RequirementCard {
  return {
    id: 'draft-1',
    version: 1,
    status: 'missing',
    summary: 'x',
    target_metrics: [],
    time_range: null,
    granularity: null,
    detail_level: null,
    scope: [],
    dimensions: [],
    analysis_methods: [],
    expected_blocks: [],
    missing_fields: [],
    assumptions: [],
    confidence: 0.5,
    confirmed_at: null,
    ...overrides,
  }
}

describe('pickQuestionField — 下一个问哪个（顺序由后端 slot_order 决定）', () => {
  it('slot_order 优先：口径排在时间范围前面', () => {
    const c = card({
      slot_order: ['metric', 'time_range'],
      missing_fields: [field('time_range', [['2024年', '2024年']]), field('metric')],
    })

    expect(pickQuestionField(c)?.key).toBe('metric')
  })

  it('没有 slot_order 的旧卡按 missing_fields 顺序问', () => {
    const c = card({
      missing_fields: [field('time_range', [['2024年', '2024年']]), field('metric')],
    })

    expect(pickQuestionField(c)?.key).toBe('time_range')
  })

  it('模型现场生成的 intent key 归到 analysis_intent 槽位', () => {
    const c = card({
      slot_order: ['analysis_intent'],
      missing_fields: [field('intent_view', [['总览', 'intent:overview']])],
    })

    expect(pickQuestionField(c)?.key).toBe('intent_view')
  })

  it('没有澄清项 → null（可以出确认条）', () => {
    expect(pickQuestionField(card({ status: 'complete' }))).toBeNull()
    expect(hasPendingQuestion(card({ status: 'complete' }))).toBe(false)
  })

  it('没有卡 → null', () => {
    expect(pickQuestionField(null)).toBeNull()
  })

  it('只有无选项的澄清项 → 不进提问条（交给完整卡渲染）', () => {
    const c = card({ slot_order: ['metric'], missing_fields: [field('metric', [])] })

    expect(pickQuestionField(c)).toBeNull()
  })
})

describe('confirmChips — 紧凑确认条的摘要', () => {
  it('按 时间 / 粒度 / 口径 / 范围 / 方法 顺序给出摘要', () => {
    const c = card({
      status: 'complete',
      time_range: '2024年',
      granularity: 'week',
      target_metrics: ['净销售额'],
      scope: ['华东'],
      analysis_methods: ['anomaly_detection'],
    })

    expect(confirmChips(c).map((chip) => chip.text)).toEqual([
      '2024年',
      '按周',
      '净销售额',
      '华东',
      '异常检测',
    ])
  })

  it('scope 是 ALL → 显示「全部区域」', () => {
    const c = card({ status: 'complete', scope: ['ALL'] })

    expect(confirmChips(c).map((chip) => chip.text)).toEqual(['全部区域'])
  })

  it('明细模式追加「逐条明细」', () => {
    const c = card({ status: 'complete', time_range: '2024年7月', detail_level: 'detail' })

    expect(confirmChips(c).map((chip) => chip.text)).toContain('逐条明细')
  })

  it('空卡 → 空摘要', () => {
    expect(confirmChips(card())).toEqual([])
    expect(confirmChips(null)).toEqual([])
  })
})
