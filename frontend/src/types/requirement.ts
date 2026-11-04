export type RequirementStatus = 'missing' | 'complete' | 'locked'

/**
 * 澄清项 key。
 *
 * 2026-11-03 起后端不再是固定枚举（澄清项由模型按 schema 现场生成，
 * `backend/app/models/requirement.py::RequirementFieldKey is str`），前端此前只写了 5 个字面量，
 * 与后端契约不一致；2026-11-04 的「一次一问」还会合成 `assumption:<key>` 这类 key，
 * 因此这里与后端对齐成 `string`，已知 key 只作为可读别名保留。
 */
export type RequirementFieldKey = string

/** 已知（服务端会内置选项的）澄清 key，仅用于可读性，不参与类型约束。 */
export type KnownRequirementFieldKey =
  | 'time_range'
  | 'scope'
  | 'metric'
  | 'comparison'
  | 'granularity'
  | 'analysis_intent'

export type RequirementFieldKind = 'single' | 'multiple'

export type SelectedValue = string | string[]

export interface RequirementOption {
  label: string
  value: string
  /** 2026-11-04：选项的补充说明（口径预览数值 / 注册表差异说明，如
   * `2.61 亿 · 订单金额未扣退款`）。label 保持干净、value 保持稳定，
   * 数值只放这里——旧后端不返回该字段也照常渲染。 */
  hint?: string | null
}

export interface RequirementMissingField {
  key: RequirementFieldKey
  label: string
  kind: RequirementFieldKind
  options: RequirementOption[]
  /** For `kind=single`, a single string (or null). For `kind=multiple`, a
   * string array (possibly empty). The server drops the field from
   * `missing_fields` and applies the value to the card's structured
   * fields (time_range / scope / target_metrics / ...) on PATCH. */
  selected_value: SelectedValue | null
  /** 2026-11-04：槽位三态之二的区分——`ambiguous` 表示「有多个合理口径」，
   * 呈现层优先提问并带数值提示；`missing` 是用户压根没提。 */
  state?: 'missing' | 'ambiguous' | null
}

export interface RequirementAssumption {
  key: string
  text: string
  accepted: boolean | null
  alternatives: RequirementOption[]
}

/** 时间粒度（镜像 backend app/models/requirement.py::RequirementGranularity）。 */
export type RequirementGranularity = 'day' | 'week' | 'month' | 'quarter' | 'year'

/** 展示粒度：detail = 逐条明细（不聚合）。镜像后端 RequirementDetailLevel。 */
export type RequirementDetailLevel = 'aggregate' | 'detail'

/**
 * 最高/最低的那一期（2026-11-04，plan 2026-11-04-top-period-detail-intent）。
 * 镜像后端 `RequirementTopPeriod`：direction=取极值方向，unit=按哪一期取。
 */
export interface RequirementTopPeriod {
  direction: 'max' | 'min'
  unit: RequirementGranularity
  metric?: string | null
}

export interface RequirementCard {
  id: string
  version: number
  status: RequirementStatus
  summary: string
  target_metrics: string[]
  time_range: string | null
  granularity: RequirementGranularity | null
  /** 2026-11-03：明细/汇总。旧会话没有该字段 → 可选。 */
  detail_level?: RequirementDetailLevel | null
  /** 2026-11-04：最高/最低的那一期。旧会话没有该字段 → 可选。 */
  top_period?: RequirementTopPeriod | null
  scope: string[]
  dimensions: string[]
  analysis_methods: string[]
  expected_blocks: string[]
  missing_fields: RequirementMissingField[]
  assumptions: RequirementAssumption[]
  confidence: number
  confirmed_at: string | null
  /** 2026-11-04：槽位三态（filled / missing / ambiguous）。旧会话没有该字段 →
   * 可选；前端「一次只问一个问题」只依赖它 + `slot_order`，不自行推导。 */
  slot_states?: Record<string, 'filled' | 'missing' | 'ambiguous'>
  /** 未填槽位的提问顺序（后端确定性排序：口径 > 时间范围 > 粒度 > 范围 > 视角）。 */
  slot_order?: string[]
}

export function isRequirementReadyForConfirmation(
  requirement: RequirementCard,
): boolean {
  return (
    requirement.status === 'complete' &&
    requirement.missing_fields.length === 0 &&
    requirement.assumptions.every((assumption) => assumption.accepted !== null)
  )
}

/**
 * Whether a `missing` draft can advance to review: every missing field
 * has a non-empty selected_value and every assumption got a verdict.
 */
export function isDraftReadyForReview(card: RequirementCard): boolean {
  const allSelected = card.missing_fields.every((field) => {
    const value = field.selected_value
    return value !== null && value !== '' && !(Array.isArray(value) && value.length === 0)
  })
  const allResolved = card.assumptions.every((assumption) => assumption.accepted !== null)
  return allSelected && allResolved
}
