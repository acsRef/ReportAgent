import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ObservabilityPage from '../ObservabilityPage'
import { fetchMetrics, fetchTraceDetail, fetchTraces } from '../../api/observabilityClient'

vi.mock('../../api/observabilityClient', () => ({
  fetchMetrics: vi.fn(),
  fetchTraces: vi.fn(),
  fetchTraceDetail: vi.fn(),
}))

const mMetrics = vi.mocked(fetchMetrics)
const mTraces = vi.mocked(fetchTraces)
const mDetail = vi.mocked(fetchTraceDetail)

const METRICS = {
  trace_total: 5,
  status_breakdown: { SUCCESS: 4, FAILED: 1 },
  success_rate: 0.8,
  avg_duration_ms: 1200,
  p95_duration_ms: 3000,
  llm_call_total: 10,
  llm_tokens_total: 5000,
  llm_avg_latency_ms: 800,
}

const TRACE = {
  trace_id: 'abc12345-deadbeef',
  session_id: 's1',
  user_query: '2024年华东销售趋势',
  status: 'SUCCESS',
  start_time: '2026-08-01T10:00:00',
  end_time: '2026-08-01T10:00:02',
  total_duration_ms: 1500,
}

const DETAIL = {
  trace: TRACE,
  spans: [
    {
      span_id: 'sp1', parent_span_id: null, span_name: 'sql_plan', span_type: 'NODE',
      start_time: '2026-08-01T10:00:00', duration_ms: 500, status: 'SUCCESS', error: null,
    },
    {
      span_id: 'sp2', parent_span_id: 'sp1', span_name: 'sql_execute', span_type: 'NODE',
      start_time: '2026-08-01T10:00:01', duration_ms: 900, status: 'SUCCESS', error: null,
    },
  ],
  llm_calls: [
    { model: 'MiniMax-M2.7', prompt_tokens: 100, completion_tokens: 50, latency_ms: 700, cost: null },
  ],
}

function setup() {
  mMetrics.mockResolvedValue(METRICS)
  mTraces.mockResolvedValue({ traces: [TRACE], limit: 50, offset: 0 })
  mDetail.mockResolvedValue(DETAIL)
}

describe('ObservabilityPage', () => {
  it('渲染指标卡与 trace 列表', async () => {
    setup()
    render(<MemoryRouter><ObservabilityPage /></MemoryRouter>)
    expect(await screen.findByText('可观测性')).toBeTruthy()
    expect(screen.getByText('Trace 总数')).toBeTruthy()
    expect(screen.getByText('完成率')).toBeTruthy()
    expect(screen.getByText('2024年华东销售趋势')).toBeTruthy()
  })

  it('点击 trace 展开 agent 执行链路与 LLM 调用', async () => {
    setup()
    render(<MemoryRouter><ObservabilityPage /></MemoryRouter>)
    fireEvent.click(await screen.findByText('2024年华东销售趋势'))
    expect(await screen.findByText(/AGENT 执行链路/)).toBeTruthy()
    expect(screen.getByText('sql_plan')).toBeTruthy()
    expect(screen.getByText('sql_execute')).toBeTruthy()
    expect(screen.getByText('MiniMax-M2.7')).toBeTruthy()
    expect(mDetail).toHaveBeenCalledWith('abc12345-deadbeef')
  })

  it('无 trace 时显示空态', async () => {
    mMetrics.mockResolvedValue(METRICS)
    mTraces.mockResolvedValue({ traces: [], limit: 50, offset: 0 })
    render(<MemoryRouter><ObservabilityPage /></MemoryRouter>)
    expect(await screen.findByText('暂无 trace 记录')).toBeTruthy()
  })
})
