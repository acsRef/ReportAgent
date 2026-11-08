/**
 * 确认后卡必须是 locked（plan 2026-11-08-analysis-quality-cleanup D3）。
 *
 * 此前 confirm 之后草稿已被后端锁定，但前端卡还是 confirm 前那份（status=complete），
 * pill 一直显示「信息完整 · 待确认」——与「已按这份需求在跑」自相矛盾。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { handleSSEEvent, type StreamEventCtx } from '../../stores/sessionEvents'
import { useAnalysisStore } from '../../stores/analysisStore'
import { analysisReducer, initialAnalysisState } from '../../stores/analysisReducer'
import type { RequirementCard } from '../../types/requirement'

function card(status: RequirementCard['status'] = 'complete'): RequirementCard {
  return {
    id: 'c1',
    version: 1,
    status,
    summary: '华东销售额',
    target_metrics: ['销售额'],
    time_range: '2024年',
    granularity: 'month',
    scope: ['华东'],
    dimensions: [],
    analysis_methods: [],
    expected_blocks: [],
    missing_fields: [],
    assumptions: [],
    confidence: 0.9,
    confirmed_at: null,
  }
}

function makeCtx(): StreamEventCtx {
  return {
    msgApi: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
    sessionId: 'sid-1',
    setSending: vi.fn(),
  }
}

describe('analysisReducer — requirement/locked', () => {
  it('有卡且未锁 → 变 locked', () => {
    const state = { ...initialAnalysisState, requirement: card('complete') }

    const next = analysisReducer(state, { type: 'requirement/locked' })

    expect(next.requirement?.status).toBe('locked')
  })

  it('没有卡时不动（闲聊轮不能凭空造卡）', () => {
    const next = analysisReducer(initialAnalysisState, { type: 'requirement/locked' })

    expect(next.requirement).toBeNull()
    expect(next).toBe(initialAnalysisState)
  })

  it('已经是 locked 时保持原引用（不触发多余渲染）', () => {
    const state = { ...initialAnalysisState, requirement: card('locked') }

    expect(analysisReducer(state, { type: 'requirement/locked' })).toBe(state)
  })
})

describe('handleSSEEvent — 进入执行相位即视为已确认', () => {
  beforeEach(() => {
    useAnalysisStore.setState(initialAnalysisState)
  })

  it.each(['generating', 'adjusting'] as const)('phase=%s 时把卡标成 locked', (phase) => {
    useAnalysisStore.setState({ ...initialAnalysisState, requirement: card('complete') })

    handleSSEEvent({ type: 'phase', phase }, makeCtx())

    expect(useAnalysisStore.getState().requirement?.status).toBe('locked')
  })

  it.each(['parsing', 'awaiting_confirm', 'report_ready'] as const)(
    'phase=%s 时不动卡（还没确认）',
    (phase) => {
      useAnalysisStore.setState({ ...initialAnalysisState, requirement: card('complete') })

      handleSSEEvent({ type: 'phase', phase }, makeCtx())

      expect(useAnalysisStore.getState().requirement?.status).toBe('complete')
    },
  )
})
