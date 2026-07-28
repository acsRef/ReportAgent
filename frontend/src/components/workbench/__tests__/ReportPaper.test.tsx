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

  it('execution_status=EMPTY renders the no-match band and rewrites the footer', async () => {
    // SQL ran cleanly but matched zero rows. The front-end must NOT
    // pretend the report has content; it shows the empty band and
    // rewrites the "SQL 已校验" footer.
    mockFetch.mockResolvedValue({
      report: {
        session_id: 'sid-1',
        version: 4,
        title: '空报告',
        status: 'done',
        execution_status: 'EMPTY',
        report_payload: {
          answer: {
            text: '查询执行成功,但未匹配到数据',
            table: { columns: [{ key: '区域', title: '区域' }], rows: [] },
          },
          execution_status: 'EMPTY',
        },
        query_snapshot: { sql: 'SELECT 1 WHERE FALSE', row_count: 0 },
        created_at: '2026-07-27T12:00:00Z',
      },
    } as never)
    render(
      <ToastProvider>
        <ReportPaper sessionId="sid-1" version={4} requirement={REQUIREMENT} />
      </ToastProvider>,
    )
    // Both the .wb-empty-band AND the table block carry "未找到匹配记录" —
    // use the band as the canonical marker.
    const matches = await screen.findAllByText('未找到匹配记录')
    expect(matches.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('查询已执行 · 未匹配到数据')).toBeTruthy()
    // Footer is rewritten — the legacy "SQL 已校验" copy is gone.
    expect(screen.queryByText(/SQL 已校验/)).toBeNull()
  })

  it('status=error (historical FAILED version) renders error band with tried SQL', async () => {
    // When the user revisits an old failed version from the right rail,
    // ReportPaper must render the error band with the tried SQL —
    // distinct from the live SSE error event.
    mockFetch.mockResolvedValue({
      report: {
        session_id: 'sid-1',
        version: 5,
        title: '失败归档',
        status: 'error',
        execution_status: 'FAILED',
        report_payload: {
          answer: { text: '查询执行失败' },
          execution_status: 'FAILED',
          error: {
            code: 'QUERY_TIMEOUT',
            message: '查询超时,请缩小时间范围或维度后重试',
            kind: 'timeout',
          },
        },
        query_snapshot: {
          sql: 'SELECT pg_sleep(60)',
          error_kind: 'timeout',
          error: 'cancel due to statement timeout',
          row_count: 0,
          truncated: false,
        },
        created_at: '2026-07-27T12:00:00Z',
      },
    } as never)
    render(
      <ToastProvider>
        <ReportPaper sessionId="sid-1" version={5} requirement={REQUIREMENT} />
      </ToastProvider>,
    )
    // "执行失败" appears in the meta pair AND in the band — assert
    // at least one match to allow both.
    const failureMarkers = await screen.findAllByText('执行失败')
    expect(failureMarkers.length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/查询超时/)).toBeTruthy()
    // Tried SQL is shown via a collapsible <details>.
    expect(screen.getByText(/SELECT pg_sleep\(60\)/)).toBeTruthy()
    // No EVIDENCE section for failed runs.
    expect(screen.queryByText(/EVIDENCE/)).toBeNull()
  })
})
