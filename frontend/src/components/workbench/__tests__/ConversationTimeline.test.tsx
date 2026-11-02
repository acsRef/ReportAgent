import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConversationTimeline from '../ConversationTimeline'
import type { ReportVersion } from '../../../types/analysis'

// ReportPaper 自己按版本拉数据——这里只验证「点开才挂载」
vi.mock('../ReportPaper', () => ({
  default: ({ version }: { version: number }) => (
    <div data-testid={`report-${version}`}>报告内容 v{version}</div>
  ),
}))

function makeVersion(version: number, status: ReportVersion['status'] = 'done'): ReportVersion {
  return {
    id: `v${version}`,
    session_id: 'sid-1',
    version,
    parent_version: version > 1 ? version - 1 : null,
    title: version === 1 ? '报告' : '报告（调整）',
    status,
    report: { answer: {} },
    created_at: '2026-11-02T12:00:00Z',
  }
}

describe('ConversationTimeline — 历史轮次不再消失', () => {
  it('按顺序渲染每一轮的问题与版本条', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: '2024年各区域销售额', version: makeVersion(1) },
          { key: 't2', question: '增加华东对比', version: makeVersion(2) },
        ]}
      />,
    )
    expect(screen.getByText('2024年各区域销售额')).toBeTruthy()
    expect(screen.getByText('增加华东对比')).toBeTruthy()
    expect(screen.getByText('v1')).toBeTruthy()
    expect(screen.getByText('v2')).toBeTruthy()
    // 默认收起：不挂 ReportPaper
    expect(screen.queryByTestId('report-1')).toBeNull()
  })

  it('点「展开报告」才挂载对应版本，且互斥展开', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: 'q1', version: makeVersion(1) },
          { key: 't2', question: 'q2', version: makeVersion(2) },
        ]}
      />,
    )
    fireEvent.click(screen.getAllByText('展开报告')[0])
    expect(screen.getByTestId('report-1')).toBeTruthy()

    fireEvent.click(screen.getAllByText('展开报告')[0])
    expect(screen.getByTestId('report-2')).toBeTruthy()
    expect(screen.queryByTestId('report-1')).toBeNull()
  })

  it('没有版本的轮次显示「未生成报告」，失败版本标红', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: '你好', version: null },
          { key: 't2', question: '查数据', version: makeVersion(2, 'error') },
        ]}
      />,
    )
    expect(screen.getByText('未生成报告')).toBeTruthy()
    expect(screen.getByText('执行失败')).toBeTruthy()
  })

  it('没有历史轮次时不渲染任何东西', () => {
    const { container } = render(<ConversationTimeline sessionId="sid-1" turns={[]} />)
    expect(container.querySelector('.wb-history')).toBeNull()
  })
})
