import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ReportPaper from '../ReportPaper'
import { ToastProvider } from '../../atelier/Toast'
import { fetchReportVersion } from '../../../api/sessionsClient'
import type { RequirementCard } from '../../../types/requirement'

vi.mock('echarts-for-react', () => ({
  default: (props: { style?: React.CSSProperties }) => (
    <div data-testid="echarts" style={props.style} />
  ),
}))

vi.mock('../../../api/sessionsClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionsClient')>()
  return { ...actual, fetchReportVersion: vi.fn() }
})

const mockFetch = vi.mocked(fetchReportVersion)

const PAYLOAD = {
  answer: {
    text: '2024 年华东销售额领先。',
    insight: '华东区域贡献最大，**8 月回落**需要关注。',
    chart: {
      type: 'bar',
      config: {
        data: [
          { 区域: '华东', 销售额: 100 },
          { 区域: '华北', 销售额: 80 },
        ],
      },
    },
    table: {
      columns: [
        { key: '区域', title: '区域' },
        { key: '销售额', title: '销售额' },
      ],
      rows: [
        { 区域: '华东', 销售额: 100 },
        { 区域: '华北', 销售额: 80 },
        { 区域: '华南', 销售额: 60 },
        { 区域: '西南', 销售额: 40 },
        { 区域: '西北', 销售额: 20 },
      ],
    },
  },
}

const REQUIREMENT: RequirementCard = {
  id: 'd1',
  version: 3,
  status: 'locked',
  summary: 'x',
  target_metrics: ['销售额'],
  time_range: '2024年',
  scope: ['华东'],
  dimensions: [],
  analysis_methods: [],
  expected_blocks: [],
  missing_fields: [],
  assumptions: [],
  confidence: 0.9,
  confirmed_at: '2026-07-27',
}

function renderPaper(props: Partial<React.ComponentProps<typeof ReportPaper>> = {}) {
  mockFetch.mockResolvedValue({
    report: {
      session_id: 'sid-1',
      version: 2,
      title: '华东区域销售经营分析',
      status: 'done',
      report_payload: PAYLOAD,
      query_snapshot: null,
      created_at: '2026-07-27T12:00:00Z',
    },
  } as never)
  return render(
    <ToastProvider>
      <ReportPaper sessionId="sid-1" version={2} requirement={REQUIREMENT} {...props} />
    </ToastProvider>,
  )
}

describe('ReportPaper — prototype shell with real payload', () => {
  it('renders REPORT/v2 header, title and meta pairs from the requirement', async () => {
    renderPaper()
    expect(await screen.findByText('REPORT / v2')).toBeTruthy()
    expect(screen.getByText('华东区域销售经营分析')).toBeTruthy()
    // meta values share a span with their key label → assert on the pair
    const metaText = (key: string) => screen.getByText(key).parentElement?.textContent ?? ''
    expect(metaText('数据范围')).toContain('2024年')
    expect(metaText('分析范围')).toContain('华东')
    expect(metaText('报告来源')).toContain('当前会话第 2 个版本')
    expect(metaText('可信度')).toContain('高')
  })

  it('insight becomes the 核心发现 band', async () => {
    renderPaper()
    expect(await screen.findByText('核心发现')).toBeTruthy()
    expect(screen.getByText('8 月回落').tagName).toBe('STRONG')
  })

  it('numbers sections OVERVIEW / VISUALIZATION / EVIDENCE in order', async () => {
    renderPaper()
    expect(await screen.findByText('01 / OVERVIEW')).toBeTruthy()
    expect(screen.getByText('02 / VISUALIZATION')).toBeTruthy()
    expect(screen.getByText('03 / EVIDENCE')).toBeTruthy()
    expect(screen.getByTestId('echarts')).toBeTruthy()
  })

  it('evidence table shows the flat 3-row preview with counter', async () => {
    renderPaper()
    expect(await screen.findByText('显示 3 / 5 条')).toBeTruthy()
    expect(screen.getByText('展开更多明细 ↓')).toBeTruthy()
  })

  it('重新生成 triggers onAdjust with the prototype adjust text', async () => {
    const onAdjust = vi.fn()
    renderPaper({ onAdjust })
    fireEvent.click(await screen.findByText('重新生成'))
    expect(onAdjust).toHaveBeenCalledWith('使用相同需求重新生成报告')
  })

  it('insight-only payload hides empty sections without crashing', async () => {
    mockFetch.mockResolvedValue({
      report: {
        session_id: 'sid-1',
        version: 3,
        title: '简报',
        status: 'done',
        report_payload: { answer: { insight: '结论一句话' } },
        query_snapshot: null,
        created_at: '2026-07-27T12:00:00Z',
      },
    } as never)
    render(
      <ToastProvider>
        <ReportPaper sessionId="sid-1" version={3} requirement={null} />
      </ToastProvider>,
    )
    expect(await screen.findByText('核心发现')).toBeTruthy()
    expect(screen.queryByText(/OVERVIEW/)).toBeNull()
    expect(screen.queryByText(/VISUALIZATION/)).toBeNull()
    expect(screen.queryByText(/EVIDENCE/)).toBeNull()
  })
})
