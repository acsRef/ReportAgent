import { describe, expect, it } from 'vitest'
import {
  confirmChips,
  hasPendingQuestion,
  pickQuestionField,
  pickNextQuestion,
  requirementUiMode,
  topPeriodText,
} from '../slotQuestion'
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

describe('requirementUiMode — 需求区该显示什么（含 409 回归钉子）', () => {
  const openCard = card({ status: 'missing', missing_fields: [field('metric')] })
  const doneCard = card({ status: 'complete', missing_fields: [], time_range: '2024年' })

  it('awaiting_* + 有槽位待问 → question', () => {
    expect(requirementUiMode('awaiting_confirm', openCard)).toBe('question')
    expect(requirementUiMode('awaiting_missing', openCard)).toBe('question')
  })

  it('awaiting_* + 槽位都填完 → 一行确认条', () => {
    expect(requirementUiMode('awaiting_confirm', doneCard)).toBe('confirm')
  })

  it('展开详情不改变形态（仍是确认条，详情内联在消息里）', () => {
    expect(requirementUiMode('awaiting_confirm', doneCard)).toBe('confirm')
  })

  // 回归钉子（2026-11-04 实测撞 409）：新一轮还在 parsing 时，上一轮的确认条必须消失
  it('parsing 相位一律 none（不许留上一轮的确认条 → 409 SESSION_BUSY）', () => {
    expect(requirementUiMode('parsing', doneCard)).toBe('none')
    expect(requirementUiMode('parsing', openCard)).toBe('none')
  })

  it('report_ready / error / idle 也不出条（报告就是回答）', () => {
    for (const phase of ['report_ready', 'error', 'idle']) {
      expect(requirementUiMode(phase, doneCard)).toBe('none')
    }
  })

  // 2026-11-05（修订 A）：执行中不再出「只读整卡」——那一轮的信息就是助手消息里的进度。
  it('执行中不出需求区（没有 card 形态了）', () => {
    expect(requirementUiMode('generating', doneCard)).toBe('none')
    expect(requirementUiMode('adjusting', openCard)).toBe('none')
  })

  it('没有卡 → none', () => {
    expect(requirementUiMode('awaiting_confirm', null)).toBe('none')
  })

  it('pickNextQuestion 优先槽位、其次假设', () => {
    const withAssumption = card({
      status: 'complete',
      missing_fields: [],
      assumptions: [{ key: 'a1', text: '按订单金额口径', accepted: null, alternatives: [] }],
    })

    expect(pickNextQuestion(openCard)?.key).toBe('metric')
    expect(pickNextQuestion(withAssumption)?.key).toBe('assumption:a1')
    expect(pickNextQuestion(doneCard)).toBeNull()
  })
})

describe('topPeriodText — 「最高的那一期」要让人看见（plan top-period-detail-intent）', () => {
  it('销售额最高的月份 → 一句人话', () => {
    expect(topPeriodText(card({ top_period: { direction: 'max', unit: 'month', metric: '销售额' } })))
      .toBe('销售额最高的月')
  })

  it('最低 + 周', () => {
    expect(topPeriodText(card({ top_period: { direction: 'min', unit: 'week', metric: '订单数' } })))
      .toBe('订单数最低的周')
  })

  it('没有选期 → null（旧卡不受影响）', () => {
    expect(topPeriodText(card({ top_period: null }))).toBeNull()
    expect(topPeriodText(null)).toBeNull()
  })

  it('确认条把选期作为 chip 摆出来', () => {
    const chips = confirmChips(card({
      time_range: '2024年',
      granularity: 'month',
      target_metrics: ['销售额'],
      top_period: { direction: 'max', unit: 'month', metric: '销售额' },
    }))
    expect(chips.map((c) => c.text)).toContain('销售额最高的月')
  })

  // 2026-11-08（plan top-period-annotate-and-timeout-copy）：标注 ≠ 只看。
  // 不标出来的话，用户说「标注一下」却拿到只有那两期/全量的数据，没法判断我们听懂没有。
  it('annotate → 明说是「全量标注」', () => {
    expect(topPeriodText(card({
      top_period: { direction: 'max', unit: 'month', metric: '销售额', purpose: 'annotate' },
    }))).toBe('销售额最高的月（全量标注）')
  })

  it('select（或不传 purpose）→ 保持原文案', () => {
    expect(topPeriodText(card({
      top_period: { direction: 'max', unit: 'month', metric: '销售额', purpose: 'select' },
    }))).toBe('销售额最高的月')
  })
})
