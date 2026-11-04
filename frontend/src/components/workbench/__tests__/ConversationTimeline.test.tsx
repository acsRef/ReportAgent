import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConversationTimeline from '../ConversationTimeline'
import type { ReportVersion } from '../../../types/analysis'

// ReportPaper 自己按版本拉数据——这里只验证挂载/收起行为
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
  })

  // 2026-11-04（plan chat-like-conversation-flow）：刚看到的报告不能因为发下一句就消失
  it('最后一轮默认展开，更早的轮次仍收起', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: 'q1', version: makeVersion(1) },
          { key: 't2', question: 'q2', version: makeVersion(2) },
        ]}
      />,
    )

    expect(screen.getByTestId('report-2')).toBeTruthy()
    expect(screen.queryByTestId('report-1')).toBeNull()
  })

  it('expandLatest=false 时全部收起（渲染预算兜底）', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: 'q1', version: makeVersion(1) },
          { key: 't2', question: 'q2', version: makeVersion(2) },
        ]}
        expandLatest={false}
      />,
    )

    expect(screen.queryByTestId('report-1')).toBeNull()
    expect(screen.queryByTestId('report-2')).toBeNull()
  })

  it('默认展开的最后一轮也能手动收起', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: 'q1', version: makeVersion(1) },
          { key: 't2', question: 'q2', version: makeVersion(2) },
        ]}
      />,
    )
    expect(screen.getByTestId('report-2')).toBeTruthy()

    fireEvent.click(screen.getByText('收起报告'))

    expect(screen.queryByTestId('report-2')).toBeNull()
    expect(screen.getByText('查看 v2 报告')).toBeTruthy()
  })

  it('点「查看 vN 报告」展开更早的轮次', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          { key: 't1', question: 'q1', version: makeVersion(1) },
          { key: 't2', question: 'q2', version: makeVersion(2) },
        ]}
      />,
    )
    fireEvent.click(screen.getByText('查看 v1 报告'))

    expect(screen.getByTestId('report-1')).toBeTruthy()
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
