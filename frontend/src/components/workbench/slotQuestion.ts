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

/** 需求区当前该显示什么（单一判定来源，便于单测）。 */
export type RequirementUiMode = 'question' | 'confirm' | 'card' | 'none'

/**
 * 需求区形态判定（2026-11-04，plan chat-like-conversation-flow）。
 *
 * 只有 `awaiting_confirm` / `awaiting_missing`（当前草稿在等你答）才出提问条或确认条；
 * `parsing`（新一轮还在解析）一律 `none`——**上一轮的确认条若留在屏幕上会被误点**，
 * 后端会以 409 SESSION_BUSY 拒绝（实测撞过）。`report_ready` / `error` 也不出条：
 * 这轮已经结束，报告就是回答。
 *
 * 「详情」不再是第四种形态（plan requirement-details-inline）：点开详情只是在
 * 确认条**内部**展开一块内容，确认条本身还在，按钮也还在——所以 `detailsOpen`
 * 不再参与判定，`card` 只剩「执行中只读整卡」这一种来源。
 */
export function requirementUiMode(
  phase: RequirementCard['status'] | string,
  card: RequirementCard | null,
  cardLocked: boolean,
): RequirementUiMode {
  if (!card) return 'none'
  if (cardLocked) return 'card'
  const awaiting = phase === 'awaiting_confirm' || phase === 'awaiting_missing'
  if (!awaiting) return 'none'
  if (pickNextQuestion(card)) return 'question'
  return 'confirm'
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

/**
 * 需求 → **一句人话**（2026-11-05，用户：「不要卡片，直接改成对话的方式」）。
 *
 * 之前确认消息是「已按你的补充更新：维度 → 时间、区域」+ 一排参数 chip，
 * 读起来像表单回执而不是在跟人说话。这里把已确定的要素拼成一句：
 *
 *   `2024年华东的销售额，按月，做异常检测`
 *
 * 只拼有值的部分；口径若来自注册表默认值，用「（默认口径）」轻轻标一下。
 */
export function describeRequirement(card: RequirementCard | null): string {
  if (!card) return ''
  const scope = (card.scope ?? []).filter((item) => item && item !== 'ALL').join('、')
  const metrics = (card.target_metrics ?? []).join('、')
  const defaulted = (card.assumptions ?? []).some((item) =>
    String(item.key ?? '').startsWith('metric_default:'),
  )
  const subject = [scope, metrics].filter(Boolean).join('的') || '这批数据'
  const first = `${card.time_range ?? ''}${subject}`.trim()
  const parts: string[] = first ? [first] : []
  if (card.granularity) parts.push(GRANULARITY_TEXT[card.granularity] ?? card.granularity)
  const topPeriod = topPeriodText(card)
  if (topPeriod) parts.push(topPeriod)
  if (card.detail_level === 'detail') parts.push('逐条明细')
  const methods = (card.analysis_methods ?? []).map((m) => methodLabel(m))
  if (methods.length) parts.push(methods.join('、'))
  const sentence = parts.filter(Boolean).join('，')
  return defaulted ? `${sentence}（默认口径）` : sentence
}

/**
 * 最高/最低的那一期 → 一句人话（2026-11-04，plan top-period-detail-intent）。
 *
 * 用户说「我只要2024年里最多月份的那个月的明细」时，之前界面上看不到「它听懂了
 * 哪一期」——只有一句「需求清楚了」，加上一个「哪种视角？」的追问。现在把这条
 * 明确画出来：`销售额最高的月份`。
 */
export function topPeriodText(card: RequirementCard | null): string | null {
  const top = card?.top_period
  if (!top) return null
  const unit = GRANULARITY_TEXT[top.unit] ?? top.unit
  const word = top.direction === 'min' ? '最低' : '最高'
  const metric = (top.metric ?? '').trim()
  return metric ? `${metric}${word}的${unit.replace(/^按/, '')}` : `${word}的${unit}`
}

export function confirmChips(card: RequirementCard | null): ConfirmChip[] {
  if (!card) return []
  const chips: ConfirmChip[] = []
  if (card.time_range) chips.push({ key: 'time_range', text: card.time_range })
  if (card.granularity) {
    chips.push({ key: 'granularity', text: GRANULARITY_TEXT[card.granularity] ?? card.granularity })
  }
  const topPeriod = topPeriodText(card)
  if (topPeriod) chips.push({ key: 'top_period', text: topPeriod })
  if (card.target_metrics?.length) {
    // 2026-11-04：用户这一轮没提口径时不再拦下来问，而是按注册表默认口径前进——
    // 那就在 chip 上标出来（「销售额（默认口径）」），让用户一眼看到按了什么、随时能改。
    const defaulted = (card.assumptions ?? []).some((item) =>
      String(item.key ?? '').startsWith('metric_default:'),
    )
    const metrics = card.target_metrics.join('、')
    chips.push({ key: 'metric', text: defaulted ? `${metrics}（默认口径）` : metrics })
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
