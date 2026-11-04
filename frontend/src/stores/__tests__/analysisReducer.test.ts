import { describe, expect, it } from 'vitest'
import {
  analysisReducer,
  canRetryFailedAction,
  initialAnalysisState,
  isBusyPhase,
  isSettledPhase,
  type AnalysisState,
} from '../analysisReducer'
import type {
  AnalysisPhase,
  ReportVersion,
  SessionSummary,
} from '../../types/analysis'
import type { RequirementCard } from '../../types/requirement'
import type { TimelineEntry } from '../../types/report'

function makeRequirement(version = 1, status: 'missing' | 'complete' | 'locked' = 'complete'): RequirementCard {
  return {
    id: 'draft-1',  // same id so the "older version" check applies
    version,
    status,
    summary: 'test',
    target_metrics: [],
    time_range: null,
    granularity: null,
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

  // 2026-11-03 钉子：版本号只在「同一张卡」内比较——补充轮/新建轮的新卡
  // id 不同，即使版本号回落到 1 也必须替换（否则界面会永远停在旧卡上）。
  it('requirement/received replaces a different card even with a lower version', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'requirement/received', requirement: makeRequirement(5) })
    const fresh = { ...makeRequirement(1), id: 'draft-2', summary: '华东区域销售分析' }
    state = analysisReducer(state, { type: 'requirement/received', requirement: fresh })
    expect(state.requirement?.id).toBe('draft-2')
    expect(state.requirement?.summary).toBe('华东区域销售分析')
  })

  it('report/selected ignores unknown version', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'report/received', report: makeReport(1) })
    state = analysisReducer(state, { type: 'report/selected', version: 999 })
    expect(state.selectedReportVersion).toBe(1)
  })
})

it('sessions/page-received replaces sessions and sets offset/hasMore', () => {
    const page: SessionSummary[] = [
      { session_id: 's-1', title: '', phase: 'idle', msg_count: 0, updated_at: '', report_versions: [] },
      { session_id: 's-2', title: '', phase: 'idle', msg_count: 0, updated_at: '', report_versions: [] },
    ]
    const state: AnalysisState = { ...initialAnalysisState }
    const next = analysisReducer(state, { type: 'sessions/page-received', sessions: page, hasMore: true })
    expect(next.sessions).toEqual(page)
    expect(next.sessionsOffset).toBe(2)
    expect(next.hasMoreSessions).toBe(true)
    expect(next.sessionsPageLoading).toBe(false)
  })

  it('sessions/page-appended appends and accumulates offset', () => {
    const page1: SessionSummary[] = [{ session_id: 's-1', title: '', phase: 'idle', msg_count: 0, updated_at: '', report_versions: [] }]
    const page2: SessionSummary[] = [{ session_id: 's-2', title: '', phase: 'idle', msg_count: 0, updated_at: '', report_versions: [] }]
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, { type: 'sessions/page-received', sessions: page1, hasMore: true })
    state = analysisReducer(state, { type: 'sessions/page-appended', sessions: page2, hasMore: false })
    expect(state.sessions.map((s) => s.session_id)).toEqual(['s-1', 's-2'])
    expect(state.sessionsOffset).toBe(2)
    expect(state.hasMoreSessions).toBe(false)
  })

  it('sessions/page-loading toggles loading flag', () => {
    const state: AnalysisState = { ...initialAnalysisState }
    expect(state.sessionsPageLoading).toBe(false)
    const next = analysisReducer(state, { type: 'sessions/page-loading', loading: true })
    expect(next.sessionsPageLoading).toBe(true)
  })

  it('messages/received 存下完整对话，供历史时间线使用', () => {
    const messages = [
      { id: 1, role: 'user' as const, message_type: 'text', content: '2024年各区域销售额', created_at: '2026-11-02T12:00:00Z' },
      { id: 2, role: 'assistant' as const, message_type: 'report_version', content: null, created_at: '2026-11-02T12:01:00Z' },
    ]
    const next = analysisReducer(initialAnalysisState, { type: 'messages/received', messages })
    expect(next.messages).toHaveLength(2)
    expect(next.messages[0].content).toBe('2024年各区域销售额')
  })

  it('session/selected 与 analysis/reset 都清空历史消息', () => {
    const messages = [
      { id: 1, role: 'user' as const, message_type: 'text', content: 'q', created_at: 'now' },
    ]
    const withMessages: AnalysisState = { ...initialAnalysisState, messages }
    expect(analysisReducer(withMessages, { type: 'session/selected', sessionId: 's-2' }).messages).toEqual([])
    expect(analysisReducer(withMessages, { type: 'analysis/reset' }).messages).toEqual([])
  })

describe('isBusyPhase', () => {
  const busy: AnalysisPhase[] = ['parsing', 'generating', 'adjusting']
  const idle: AnalysisPhase[] = ['idle', 'awaiting_missing', 'awaiting_confirm', 'report_ready', 'error']
  for (const p of busy) it(`returns true for ${p}`, () => expect(isBusyPhase(p)).toBe(true))
  for (const p of idle) it(`returns false for ${p}`, () => expect(isBusyPhase(p)).toBe(false))
})

// 2026-11-03：后台轮询的完成判定（此前只认 report_ready/error，解析轮后台化后
// 「停止等待 → 自动刷回新卡」失效）。isSettledPhase 是这条判定的单一来源。
describe('isSettledPhase', () => {
  it('parsing 是忙（解析轮后台跑着，不能当作完成）', () =>
    expect(isSettledPhase('parsing')).toBe(false))
  it('generating / adjusting 是忙', () => {
    expect(isSettledPhase('generating')).toBe(false)
    expect(isSettledPhase('adjusting')).toBe(false)
  })
  it('awaiting_* / report_ready / error / idle 都是稳定态', () => {
    for (const p of ['idle', 'awaiting_missing', 'awaiting_confirm', 'report_ready', 'error']) {
      expect(isSettledPhase(p)).toBe(true)
    }
  })
})

describe('canRetryFailedAction', () => {
  const confirmError = { code: 'QUERY_FAILED', message: '查询未返回数据', recoverable: true, failed_action: 'confirm' } as const

  it('true for recoverable confirm failure in error phase', () => {
    expect(canRetryFailedAction({ phase: 'error', error: { ...confirmError } })).toBe(true)
  })

  it('true for recoverable requirement failures (new/supplement/adjust)', () => {
    // P-1: REQUIREMENT_INCOMPLETE 以 failed_action = 当前 mode 出现且 recoverable，
    // 也应可重试。
    for (const action of ['new', 'supplement', 'adjust'] as const) {
      expect(canRetryFailedAction({
        phase: 'error',
        error: { ...confirmError, failed_action: action },
      })).toBe(true)
    }
  })

  it('false when the failed action is not retryable', () => {
    expect(canRetryFailedAction({
      phase: 'error',
      error: { ...confirmError, failed_action: 'retry' },
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

// ── 执行过程（trace）状态（2026-11-04，plan trace-visible-reasoning）─────────
describe('timeline/received — 执行过程步骤', () => {
  const entry = (nodeName: string, status: TimelineEntry['status'], timestamp = 1000) => ({
    id: `${nodeName}-${status}`,
    nodeName,
    status,
    timestamp,
  })

  it('同一节点 running → success 只保留一行（按 nodeName 去重）', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    state = analysisReducer(state, {
      type: 'timeline/received',
      entry: entry('解析需求', 'running'),
    })
    state = analysisReducer(state, {
      type: 'timeline/received',
      entry: entry('解析需求', 'success'),
    })

    expect(state.timeline).toHaveLength(1)
    expect(state.timeline[0].status).toBe('success')
  })

  it('不同节点按到达顺序追加', () => {
    let state: AnalysisState = { ...initialAnalysisState }
    for (const name of ['安全检查', '识别意图', '解析需求']) {
      state = analysisReducer(state, {
        type: 'timeline/received',
        entry: entry(name, 'success'),
      })
    }

    expect(state.timeline.map((e) => e.nodeName)).toEqual(['安全检查', '识别意图', '解析需求'])
  })

  it('收到步骤会清掉上一次的异常说明', () => {
    let state: AnalysisState = {
      ...initialAnalysisState,
      timelineNote: '连接中断，任务可能在后台继续',
    }
    state = analysisReducer(state, {
      type: 'timeline/received',
      entry: entry('解析需求', 'running'),
    })

    expect(state.timelineNote).toBeNull()
  })

  it('timeline/cleared 清空步骤与说明（新一轮开始）', () => {
    let state: AnalysisState = {
      ...initialAnalysisState,
      timeline: [entry('解析需求', 'success')] as TimelineEntry[],
      timelineNote: 'x',
    }
    state = analysisReducer(state, { type: 'timeline/cleared' })

    expect(state.timeline).toEqual([])
    expect(state.timelineNote).toBeNull()
  })

  it('timeline/closed(stopped) 把进行中步骤标成 pending 并给说明', () => {
    let state: AnalysisState = {
      ...initialAnalysisState,
      timeline: [entry('撰写报告内容', 'running')] as TimelineEntry[],
    }
    state = analysisReducer(state, { type: 'timeline/closed', reason: 'stopped' })

    expect(state.timeline[0].status).toBe('pending')
    expect(state.timelineNote).toContain('已停止显示')
  })

  it('timeline/closed(interrupted) 标成 error 并说明可能在后台继续', () => {
    let state: AnalysisState = {
      ...initialAnalysisState,
      timeline: [entry('解析需求', 'running')] as TimelineEntry[],
    }
    state = analysisReducer(state, { type: 'timeline/closed', reason: 'interrupted' })

    expect(state.timeline[0].status).toBe('error')
    expect(state.timelineNote).toContain('连接中断')
  })

  it('timeline/closed(done) 把进行中步骤收成 success 且不留说明', () => {
    let state: AnalysisState = {
      ...initialAnalysisState,
      timeline: [entry('汇总报告', 'running')] as TimelineEntry[],
    }
    state = analysisReducer(state, { type: 'timeline/closed', reason: 'done' })

    // 正常收尾的最后一步只是没来得及发 success 帧——不能标成失败
    expect(state.timeline[0].status).toBe('success')
    expect(state.timelineNote).toBeNull()
  })
})
