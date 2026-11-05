import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConversationTimeline from '../ConversationTimeline'
import type { ChatTurn } from '../historyTurns'
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

/** 一轮历史（2026-11-05 修订 A：ChatTurn = 问 + 答 + 过程 + 版本）。 */
function turn(
  key: string,
  question: string,
  version: ReportVersion | null,
  answer: string | null = null,
): ChatTurn {
  return { key, question, answer, trace: [], traceNote: null, status: 'done', version, at: null }
}

describe('ConversationTimeline — 历史轮次不再消失', () => {
  it('按顺序渲染每一轮的问题与版本条', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          turn('t1', '2024年各区域销售额', makeVersion(1)),
          turn('t2', '增加华东对比', makeVersion(2)),
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
          turn('t1', 'q1', makeVersion(1)),
          turn('t2', 'q2', makeVersion(2)),
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
          turn('t1', 'q1', makeVersion(1)),
          turn('t2', 'q2', makeVersion(2)),
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
          turn('t1', 'q1', makeVersion(1)),
          turn('t2', 'q2', makeVersion(2)),
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
          turn('t1', 'q1', makeVersion(1)),
          turn('t2', 'q2', makeVersion(2)),
        ]}
      />,
    )
    fireEvent.click(screen.getByText('查看 v1 报告'))

    expect(screen.getByTestId('report-1')).toBeTruthy()
  })

  // 2026-11-04（plan history-turn-pairing）：没有报告的轮次不再画「未生成报告」边框条
  // ——没有可展开内容，那条小条只会压在当轮助手消息上方（用户截图即此）。
  it('没有版本的轮次只留提问气泡、不画空报告条；失败版本仍标红', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          turn('t1', '你好', null),
          turn('t2', '查数据', makeVersion(2, 'error')),
        ]}
      />,
    )
    expect(screen.getByText('你好')).toBeTruthy()
    expect(screen.queryByText('未生成报告')).toBeNull()
    expect(document.querySelectorAll('.wb-history-report')).toHaveLength(1)
    expect(screen.getByText('执行失败')).toBeTruthy()
  })

  // 2026-11-05（plan conversation-transcript-messages 修订 A）：一轮 = 问 + 答 + 过程。
  // 历史轮次此前只有一个问题气泡 + 版本条，助手说过的话在界面上不存在。
  it('历史轮次渲染助手那句话与过程摘要（不再只有版本条）', () => {
    render(
      <ConversationTimeline
        sessionId="sid-1"
        turns={[
          {
            key: 't1',
            question: '2024年各区域销售额',
            answer: '好，我理解为：2024年的销售额。',
            trace: [{ id: 'x', nodeName: '解析需求', status: 'success', timestamp: 1 }],
            traceNote: null,
            status: 'done',
            version: null,
            at: null,
          },
        ]}
      />,
    )
    expect(screen.getByText('2024年各区域销售额')).toBeTruthy()
    expect(screen.getByText('好，我理解为：2024年的销售额。')).toBeTruthy()
    // 2026-11-05（plan reasoning-chain-display）：历史轮的思考块默认收起，只留一行「已思考（用时 N 秒）」
    expect(screen.getByText(/已思考（用时/)).toBeTruthy()
  })

  it('没有历史轮次时不渲染任何东西', () => {
    const { container } = render(<ConversationTimeline sessionId="sid-1" turns={[]} />)
    expect(container.querySelector('.wb-history')).toBeNull()
  })
})
