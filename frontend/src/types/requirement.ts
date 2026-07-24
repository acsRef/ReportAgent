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

export interface RequirementCard {
  id: string
  version: number
  status: RequirementStatus
  summary: string
  target_metrics: string[]
  time_range: string | null
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
