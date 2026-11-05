import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import TracePanel from '../TracePanel'
import type { TimelineEntry } from '../../../types/report'

function entry(
  nodeName: string,
  status: TimelineEntry['status'],
  options: Partial<TimelineEntry> = {},
): TimelineEntry {
  return {
    id: `${nodeName}-${status}`,
    nodeName,
    status,
    timestamp: 1_700_000_000_000,
    ...options,
  }
}

const RUNNING: TimelineEntry[] = [
  { ...entry('安全检查', 'success'), kind: 'agent', timestamp: 1_700_000_000_000 },
  { ...entry('识别意图', 'success'), kind: 'agent', timestamp: 1_700_000_001_200 },
  { ...entry('解析需求', 'running'), kind: 'agent', timestamp: 1_700_000_009_000 },
]

const DONE: TimelineEntry[] = [
  { ...entry('解析需求', 'success'), kind: 'agent', timestamp: 1_700_000_000_000 },
  { ...entry('口径预览', 'success'), kind: 'tool', timestamp: 1_700_000_002_500 },
  { ...entry('生成 SQL', 'success'), kind: 'sql', timestamp: 1_700_000_006_000 },
]

describe('TracePanel — 「已思考（用时 N 秒）」+ 思考链路（2026-11-05 重塑）', () => {
  it('运行中：一行「正在思考（当前步骤）」，默认不铺开内容', () => {
    render(<TracePanel entries={RUNNING} running />)

    expect(screen.getByText('正在思考（解析需求）')).toBeTruthy()
    expect(screen.queryByText('安全检查')).toBeNull()
  })

  // 2026-11-05（plan live-session-issues D2）：真机出现过 90s 只有一个静态词，用户以为卡死。
  it('运行中超过 1 秒后显示已等待秒数', () => {
    const justStarted: TimelineEntry[] = [
      { ...entry('理解意图', 'running'), timestamp: Date.now() - 5_000 },
    ]
    render(<TracePanel entries={justStarted} running />)

    expect(screen.getByText(/正在思考（理解意图 · [456]s）/)).toBeTruthy()
  })

  it('结束后：一行「已思考（用时 N 秒）」——讲时间，不讲步骤数', () => {
    render(<TracePanel entries={DONE} running={false} />)

    expect(screen.getByText('已思考（用时 6 秒）')).toBeTruthy()
  })

  // 2026-11-05（plan streaming-reasoning）：链路是边跑边长的，得让用户知道还没说完。
  it('运行中且有链路内容时，链路区带「正在输出…」尾巴', () => {
    const streaming: TimelineEntry[] = [
      { ...entry('理解意图 · 思考', 'running'), reasoning: '我们只需要输出JSON。' },
    ]
    render(<TracePanel entries={streaming} running autoOpen />)

    expect(screen.getByText('正在输出…')).toBeTruthy()
    expect(screen.getByText(/我们只需要输出JSON/)).toBeTruthy()
  })

  it('跑完就不再有「正在输出…」', () => {
    const done: TimelineEntry[] = [
      { ...entry('理解意图 · 思考', 'success'), reasoning: '想完了。' },
    ]
    render(<TracePanel entries={done} running={false} autoOpen />)

    expect(screen.queryByText('正在输出…')).toBeNull()
  })

  it('展开后才显示每一步（含状态）', () => {
    render(<TracePanel entries={DONE} running={false} />)
    fireEvent.click(screen.getByText('已思考（用时 6 秒）'))

    expect(screen.getByText('生成 SQL')).toBeTruthy()
    expect(screen.getAllByText('完成')).toHaveLength(3)
  })

  it('步骤展开后显示这一步的结论（detail）', () => {
    const withDetail: TimelineEntry[] = [
      {
        ...entry('识别意图', 'success'),
        kind: 'agent',
        detail: '意图=report（业务问题）',
      },
    ]
    render(<TracePanel entries={withDetail} running={false} />)
    fireEvent.click(screen.getByText(/已思考/))

    expect(screen.getByText('意图=report（业务问题）')).toBeTruthy()
  })

  // 2026-11-05：用户要的是「模型原文链路」，不是「第几步做了什么」。
  it('展开后先渲染模型原文思考链路，再渲染执行步骤', () => {
    const withReasoning: TimelineEntry[] = [
      { ...entry('理解意图 · 思考', 'success'), kind: 'agent', detail: '模型原文 12 字' },
      {
        ...entry('理解意图 · 思考', 'success'),
        kind: 'agent',
        reasoning: '用户原话是查销售额，这是新任务。',
      },
    ]
    render(<TracePanel entries={withReasoning} running={false} />)
    fireEvent.click(screen.getByText(/已思考/))

    expect(screen.getByText('思考链路')).toBeTruthy()
    expect(screen.getByText('用户原话是查销售额，这是新任务。')).toBeTruthy()
    expect(screen.getByText('执行步骤')).toBeTruthy()
  })

  it('多个节点都有原文时，每段带节点名', () => {
    const multi: TimelineEntry[] = [
      { ...entry('理解意图 · 思考', 'success'), reasoning: '先判断是不是新任务。' },
      { ...entry('规划查询', 'success'), reasoning: '再决定按区域分组。' },
    ]
    render(<TracePanel entries={multi} running={false} />)
    fireEvent.click(screen.getByText(/已思考/))

    const chain = within(screen.getByLabelText('思考链路'))
    expect(chain.getByText('理解意图 · 思考')).toBeTruthy()
    expect(chain.getByText('规划查询')).toBeTruthy()
    expect(chain.getByText('先判断是不是新任务。')).toBeTruthy()
  })

  it('markdown 原文按 markdown 渲染（加粗不显示星号）', () => {
    const md: TimelineEntry[] = [
      {
        ...entry('理解意图 · 思考', 'success'),
        reasoning: '**关键区分**：配方是设计，批次是执行。',
      },
    ]
    render(<TracePanel entries={md} running={false} />)
    fireEvent.click(screen.getByText(/已思考/))

    expect(screen.getByText('关键区分')).toBeTruthy()
  })

  it('autoOpen：当轮默认展开，历史默认收起；用户点过之后以用户为准', () => {
    const { rerender } = render(<TracePanel entries={DONE} running={false} autoOpen />)
    expect(screen.getByText('生成 SQL')).toBeTruthy()

    fireEvent.click(screen.getByText(/已思考/))
    expect(screen.queryByText('生成 SQL')).toBeNull()

    // autoOpen 再次为真也不会把用户收起来的面板抢回去
    rerender(<TracePanel entries={DONE} running={false} autoOpen />)
    expect(screen.queryByText('生成 SQL')).toBeNull()
  })

  it('失败：一行点名中断在哪一步（带用时）', () => {
    const failed: TimelineEntry[] = [
      ...RUNNING.slice(0, 2),
      { ...entry('解析需求', 'error'), kind: 'agent', timestamp: 1_700_000_009_000 },
    ]
    render(<TracePanel entries={failed} running={false} />)

    expect(screen.getByText('思考中断于「解析需求」（用时 9 秒）')).toBeTruthy()
  })

  it('停止：一行说明后台继续', () => {
    const stopped: TimelineEntry[] = [
      { ...entry('撰写报告内容', 'pending'), kind: 'report' },
    ]
    render(<TracePanel entries={stopped} running={false} />)

    expect(screen.getByText(/已停止于「撰写报告内容」/)).toBeTruthy()
  })

  it('中断/停止说明（note）显示在摘要下方', () => {
    render(
      <TracePanel
        entries={RUNNING}
        note="连接中断，任务可能在后台继续；恢复后会自动更新"
        running={false}
      />,
    )

    expect(screen.getByText(/连接中断/)).toBeTruthy()
  })

  it('没有步骤也没有说明时不渲染（不占位）', () => {
    const { container } = render(<TracePanel entries={[]} running={false} />)

    expect(container.querySelector('.wb-think')).toBeNull()
  })
})
