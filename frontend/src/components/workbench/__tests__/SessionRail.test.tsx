import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import SessionRail from '../SessionRail'
import type { ReportVersion, SessionSummary } from '../../../types/analysis'

function makeSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 'sid-1',
    title: '华东销售分析',
    phase: 'report_ready',
    msg_count: 4,
    updated_at: new Date().toISOString(),
    report_versions: [],
    ...overrides,
  } as SessionSummary
}

function makeVersion(version: number): ReportVersion {
  return {
    id: `r-${version}`,
    session_id: 'sid-1',
    version,
    parent_version: version > 1 ? version - 1 : null,
    title: '报告',
    status: 'done',
    created_at: new Date().toISOString(),
  } as ReportVersion
}

const baseProps = () => ({
  sessions: [makeSession()],
  activeSessionId: 'sid-1',
  reportVersions: [] as ReportVersion[],
  selectedReportVersion: null as number | null,
  onSelect: vi.fn(),
  onSelectVersion: vi.fn(),
  onNew: vi.fn(),
})

describe('SessionRail', () => {
  it('buckets fresh sessions under 今天 with status pill + footer count', () => {
    render(<SessionRail {...baseProps()} />)
    expect(screen.getByText('今天')).toBeTruthy()
    expect(screen.getByText('已完成')).toBeTruthy() // report_ready pill
    expect(screen.getByText('对话会自动保存 · 当前 1 个分析任务')).toBeTruthy()
    expect(screen.getByText('＋ 新建分析')).toBeTruthy()
  })

  it('active session shows the version-box with 当前 marker', () => {
    const props = {
      ...baseProps(),
      reportVersions: [makeVersion(1), makeVersion(2)],
      selectedReportVersion: 2,
    }
    render(<SessionRail {...props} />)
    expect(screen.getByText('报告版本')).toBeTruthy()
    expect(screen.getByText('仅回看，不重新生成')).toBeTruthy()
    expect(screen.getByText('当前')).toBeTruthy()
    expect(screen.queryByText('查看全部版本 →')).toBeNull() // ≤3 versions
  })

  it('>3 versions shows 查看全部版本 → opening a modal with all versions', () => {
    const props = {
      ...baseProps(),
      reportVersions: [makeVersion(1), makeVersion(2), makeVersion(3), makeVersion(4)],
      selectedReportVersion: 4,
    }
    render(<SessionRail {...props} />)
    // only the latest 3 inline
    expect(screen.queryByText('v1 · 报告')).toBeNull()
    fireEvent.click(screen.getByText('查看全部版本 →'))
    const modal = screen.getByRole('dialog')
    expect(within(modal).getByText('v1 · 报告')).toBeTruthy()
    expect(within(modal).getByText('v4 · 报告')).toBeTruthy()
  })

  it('version click dispatches onSelectVersion', () => {
    const onSelectVersion = vi.fn()
    render(
      <SessionRail
        {...baseProps()}
        reportVersions={[makeVersion(1), makeVersion(2)]}
        selectedReportVersion={2}
        onSelectVersion={onSelectVersion}
      />,
    )
    fireEvent.click(screen.getByText('v1 · 报告'))
    expect(onSelectVersion).toHaveBeenCalledWith(1)
  })
})
