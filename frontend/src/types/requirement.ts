export type RequirementStatus = 'missing' | 'complete' | 'locked'

export type RequirementFieldKey =
  | 'time_range'
  | 'scope'
  | 'metric'
  | 'comparison'
  | 'granularity'

export type RequirementFieldKind = 'single' | 'multiple'

export type SelectedValue = string | string[]

export interface RequirementOption {
  label: string
  value: string
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
  scope: string[]
  dimensions: string[]
  analysis_methods: string[]
  expected_blocks: string[]
  missing_fields: RequirementMissingField[]
  assumptions: RequirementAssumption[]
  confidence: number
  confirmed_at: string | null
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
