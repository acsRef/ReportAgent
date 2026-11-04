/**
 * 槽位提问的选择器（2026-11-04，plan 2026-11-04-requirement-slot-contract Phase 5）。
 *
 * 呈现层改成「一次只问一个」之后，「下一个问哪个」不能由前端拍脑袋——
 * 顺序由后端 `RequirementCard.slot_order` 下发（口径 > 时间范围 > 粒度 > 范围 > 视角），
 * 这里只做**确定性映射**：槽位名 → 卡上对应的 missing_field。纯函数，便于单测。
 */
import type { RequirementCard, RequirementMissingField } from '../../types/requirement'

/** 后端槽位名 → 卡上可能的 clarification key（模型现场生成的 key 用前缀兜底）。 */
const SLOT_TO_KEYS: Record<string, (key: string) => boolean> = {
  metric: (key) => key === 'metric',
  time_range: (key) => key === 'time_range',
  scope: (key) => key === 'scope',
  granularity: (key) => key === 'granularity',
  analysis_intent: (key) =>
    key === 'analysis_intent' || key.startsWith('intent') || key.startsWith('analysis'),
}

/**
 * 当前该问的槽位（`null` = 没有待问项，可以进入确认条）。
 *
 * 顺序优先用后端 `slot_order`；旧卡没有该字段时退回 missing_fields 的原始顺序。
 * 只有带选项的澄清项才进提问条——没有选项的（理论上不该出现）留给完整卡渲染。
 */
export function pickQuestionField(card: RequirementCard | null): RequirementMissingField | null {
  if (!card) return null
  const fields = card.missing_fields ?? []
  if (fields.length === 0) return null

  const order = card.slot_order ?? []
  if (order.length === 0) {
    // 旧卡没有 slot_order（后端老版本 / 历史会话）→ 保持既有渲染顺序，
    // 不能自己按新优先级重排：那会让同一张旧卡突然换个问题先问。
    return fields.find((field) => field.options.length > 0) ?? null
  }

  for (const slot of order) {
    const matches = SLOT_TO_KEYS[slot]
    if (!matches) continue
    const field = fields.find((candidate: RequirementMissingField) => matches(candidate.key))
    if (field && field.options.length > 0) return field
  }
  // 兜底：slot_order 里没覆盖到的 key（模型新造的 key）按原顺序问
  return fields.find((field: RequirementMissingField) => field.options.length > 0) ?? null
}

/** 卡上是否还有待问的槽位（false 且 status=complete → 可以出确认条）。 */
export function hasPendingQuestion(card: RequirementCard | null): boolean {
  return pickQuestionField(card) !== null
}

/** 未表态假设 → 合成一个问题（「就按这个 / 我要改」），让确认条不被假设卡住。 */
export const ASSUMPTION_KEY_PREFIX = 'assumption:'

export function pickPendingAssumption(card: RequirementCard | null): RequirementMissingField | null {
  if (!card) return null
  const pending = (card.assumptions ?? []).find(
    (assumption) => assumption.accepted === null,
  )
  if (!pending) return null
  return {
    key: `${ASSUMPTION_KEY_PREFIX}${pending.key}`,
    label: pending.text,
    kind: 'single',
    state: 'ambiguous',
    options: [
      { label: '就按这个', value: 'accept' },
      { label: '我要改口径', value: 'revise' },
    ],
    selected_value: null,
  }
}

/** 一次只问一个：优先问未填槽位，其次问未表态的假设。 */
export function pickNextQuestion(card: RequirementCard | null): RequirementMissingField | null {
  return pickQuestionField(card) ?? pickPendingAssumption(card)
}

/** 已经确定下来的槽位摘要（确认条用）：时间 / 粒度 / 口径 / 范围 / 角度。 */
export interface ConfirmChip {
  key: string
  text: string
}

const GRANULARITY_TEXT: Record<string, string> = {
  day: '按日',
  week: '按周',
  month: '按月',
  quarter: '按季度',
  year: '按年',
}

const METHOD_TEXT: Record<string, string> = {
  trend_analysis: '趋势分析',
  group_compare: '对比/排名',
  anomaly_detection: '异常检测',
  chart_advisor: '图表建议',
  insight_analyst: '数值摘要',
}

/** 分析角度/工具 id → 中文标签（卡片与确认条共用，避免界面出现 group_compare 这种 id）。 */
export function methodLabel(method: string): string {
  return METHOD_TEXT[method] ?? method
}

export function confirmChips(card: RequirementCard | null): ConfirmChip[] {
  if (!card) return []
  const chips: ConfirmChip[] = []
  if (card.time_range) chips.push({ key: 'time_range', text: card.time_range })
  if (card.granularity) {
    chips.push({ key: 'granularity', text: GRANULARITY_TEXT[card.granularity] ?? card.granularity })
  }
  if (card.target_metrics?.length) {
    chips.push({ key: 'metric', text: card.target_metrics.join('、') })
  }
  if (card.scope?.length) {
    const scope = card.scope.includes('ALL') ? '全部区域' : card.scope.join('、')
    chips.push({ key: 'scope', text: scope })
  }
  if (card.analysis_methods?.length) {
    const methods = card.analysis_methods
      .map((method: string) => methodLabel(method))
      .join(' · ')
    chips.push({ key: 'analysis_methods', text: methods })
  }
  if (card.detail_level === 'detail') {
    chips.push({ key: 'detail_level', text: '逐条明细' })
  }
  return chips
}
