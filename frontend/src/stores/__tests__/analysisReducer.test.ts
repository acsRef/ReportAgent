import { describe, expect, it } from 'vitest'
import {
  analysisReducer,
  canRetryFailedAction,
  initialAnalysisState,
  isBusyPhase,
  type AnalysisState,
} from '../analysisReducer'
import type {
  AnalysisPhase,
  ReportVersion,
  SessionSummary,
} from '../../types/analysis'
import type { RequirementCard } from '../../types/requirement'

function makeRequirement(version = 1, status: 'missing' | 'complete' | 'locked' = 'complete'): RequirementCard {
  return {
    id: 'draft-1',  // same id so the "older version" check applies
    version,
    status,
    summary: 'test',
    target_metrics: [],
    time_range: null,
    scope: [],
    dimensions: [],
    analysis_methods: [],
    expected_blocks: [],
    missing_fields: [],
    assumptions: [],
    confidence: 0.9,
    confirmed_at: null,
  }
}

function makeReport(version: number): ReportVersion {
  return {
    id: `r-${version}`,
    session_id: 's-1',
    version,
    parent_version: version === 1 ? null : version - 1,
    title: `v${version}`,
    status: 'done',
    report: { answer: { text: `v${version}` } },
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('analysisReducer', () => {
  it('phase/received updates phase and clears error when not error', () => {
    const state: AnalysisState = { ...initialAnalysisState, error: { code: 'X', message: 'old', recoverable: false, failed_action: 'new' } }
    const next = analysisReducer(state, { type: 'phase/received', phase: 'parsing' })
    expect(next.phase).toBe('parsing')
    expect(next.error).toBeNull()
  })

  it('session/selected resets state but keeps sessions list', () => {
    const sessions: SessionSummary[] = [
      { session_id: 's-1', title: '', phase: 'idle', msg_count: 0, updated_at: '', report_versions: [] },
    ]
    const state: AnalysisState = {
      ...initialAnalysisState,
      sessions,
      requirement: makeRequirement(),
      reportVersions: [makeReport(1)],
    }
    const next = analysisReducer(state, { type: 'session/selected', sessionId: 's-1' })
    expect(next.activeSessionId).toBe('s-1')
    expect(next.requirement).toBeNull()
    expect(next.reportVersions).toEqual([])
    expect(next.sessions).toEqual(sessions)
  })

  it('report/received appends and selects the new version', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'report/received', report: makeReport(1) })
    state = analysisReducer(state, { type: 'report/received', report: makeReport(2) })
    expect(state.reportVersions.map((r) => r.version)).toEqual([1, 2])
    expect(state.selectedReportVersion).toBe(2)
    expect(state.phase).toBe('report_ready')
  })

  it('report/received replaces existing version with same id (last-wins)', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'report/received', report: makeReport(1) })
    const updated = { ...makeReport(1), title: 'updated' }
    state = analysisReducer(state, { type: 'report/received', report: updated })
    expect(state.reportVersions).toHaveLength(1)
    expect(state.reportVersions[0].title).toBe('updated')
  })

  it('analysis/failed sets error phase but keeps reportVersions', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'report/received', report: makeReport(1) })
    state = analysisReducer(state, {
      type: 'analysis/failed',
      error: { code: 'X', message: 'oops', recoverable: true, failed_action: 'new' },
    })
    expect(state.phase).toBe('error')
    expect(state.reportVersions).toHaveLength(1)
    expect(state.error?.code).toBe('X')
  })

  it('requirement/received ignores older versions', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'requirement/received', requirement: makeRequirement(3) })
    state = analysisReducer(state, { type: 'requirement/received', requirement: makeRequirement(2) })
    expect(state.requirement?.version).toBe(3)
  })

  it('requirement/received same version replaces', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'requirement/received', requirement: makeRequirement(2) })
    const replaced = { ...makeRequirement(2), summary: 'updated' }
    state = analysisReducer(state, { type: 'requirement/received', requirement: replaced })
    expect(state.requirement?.summary).toBe('updated')
  })

  it('report/selected ignores unknown version', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'report/received', report: makeReport(1) })
    state = analysisReducer(state, { type: 'report/selected', version: 999 })
    expect(state.selectedReportVersion).toBe(1)
  })
})

describe('isBusyPhase', () => {
  const busy: AnalysisPhase[] = ['parsing', 'generating', 'adjusting']
  const idle: AnalysisPhase[] = ['idle', 'awaiting_missing', 'awaiting_confirm', 'report_ready', 'error']
  for (const p of busy) it(`returns true for ${p}`, () => expect(isBusyPhase(p)).toBe(true))
  for (const p of idle) it(`returns false for ${p}`, () => expect(isBusyPhase(p)).toBe(false))
})

describe('canRetryFailedAction', () => {
  const confirmError = { code: 'QUERY_FAILED', message: '查询未返回数据', recoverable: true, failed_action: 'confirm' } as const

  it('true for recoverable confirm failure in error phase', () => {
    expect(canRetryFailedAction({ phase: 'error', error: { ...confirmError } })).toBe(true)
  })

  it('false when the failed action is not confirm/sql', () => {
    expect(canRetryFailedAction({
      phase: 'error',
      error: { ...confirmError, failed_action: 'new' },
    })).toBe(false)
  })

  it('true for recoverable sql failure in error phase', () => {
    // Structured SQL errors (timeout / connection / object) emitted by
    // the new _build_sse_error helper also qualify for retry.
    expect(canRetryFailedAction({
      phase: 'error',
      error: { ...confirmError, failed_action: 'sql', kind: 'timeout' },
    })).toBe(true)
  })

  it('false when the error is not recoverable', () => {
    expect(canRetryFailedAction({
      phase: 'error',
      error: { ...confirmError, recoverable: false },
    })).toBe(false)
  })

  it('false outside the error phase even with a stale error object', () => {
    expect(canRetryFailedAction({ phase: 'report_ready', error: { ...confirmError } })).toBe(false)
  })

  it('false when error is null', () => {
    expect(canRetryFailedAction({ phase: 'error', error: null })).toBe(false)
  })
})
