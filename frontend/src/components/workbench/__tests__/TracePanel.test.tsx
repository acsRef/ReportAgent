import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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
  { ...entry('安全检查', 'success'), kind: 'agent' as const, timestamp: 1_700_000_000_000 },
  { ...entry('识别意图', 'success'), kind: 'agent' as const, timestamp: 1_700_000_001_200 },
  { ...entry('解析需求', 'running'), kind: 'agent' as const, timestamp: 1_700_000_009_000 },
]

const DONE: TimelineEntry[] = [
  { ...entry('解析需求', 'success'), kind: 'agent' as const, timestamp: 1_700_000_000_000 },
  { ...entry('口径预览', 'success'), kind: 'tool' as const, timestamp: 1_700_000_002_500 },
  { ...entry('生成 SQL', 'success'), kind: 'sql' as const, timestamp: 1_700_000_006_000 },
]

describe('TracePanel — 看得见的执行过程', () => {
  it('运行中默认展开，头部显示当前步骤', () => {
    render(<TracePanel entries={RUNNING} running />)

    expect(screen.getByText('正在执行 · 解析需求')).toBeTruthy()
    expect(screen.getByText('安全检查')).toBeTruthy()
    expect(screen.getByText('解析需求')).toBeTruthy()
    expect(screen.getByText('进行中')).toBeTruthy()
  })

  it('结束后摘要显示步数与总耗时，可展开逐步骤再收起', () => {
    render(<TracePanel entries={DONE} running={false} />)

    // 正常结束默认收起（豆包式一行摘要），细节按需展开
    expect(screen.getByText('执行过程 · 3 步 · 6.0s')).toBeTruthy()
    expect(screen.queryByText('生成 SQL')).toBeNull()

    fireEvent.click(screen.getByText('展开'))
    expect(screen.getByText('生成 SQL')).toBeTruthy()

    fireEvent.click(screen.getByText('收起'))
    expect(screen.queryByText('生成 SQL')).toBeNull()
  })

  it('步骤带中文类别标签与每步耗时', () => {
    render(<TracePanel entries={DONE} running={false} />)
    fireEvent.click(screen.getByText('展开'))

    expect(screen.getByText('理解')).toBeTruthy()
    expect(screen.getByText('查询')).toBeTruthy()
    expect(screen.getByText('≈2.5s')).toBeTruthy()
  })

  it('失败时自动展开并点名中断在哪一步（异常定位）', () => {
    const failed: TimelineEntry[] = [
      ...RUNNING.slice(0, 2),
      { ...entry('解析需求', 'error'), kind: 'agent' },
    ]
    render(<TracePanel entries={failed} running={false} />)

    expect(screen.getByText('执行过程 · 中断于「解析需求」')).toBeTruthy()
    expect(screen.getByText('失败')).toBeTruthy()
  })

  it('中断说明（note）显示在头部下方', () => {
    render(
      <TracePanel
        entries={BUILD_INTERRUPTED}
        note="连接中断，任务可能在后台继续；恢复后会自动更新"
        running={false}
      />,
    )

    expect(screen.getByText(/连接中断/)).toBeTruthy()
  })

  it('停止的步骤显示「已停止」（不是成功也不是失败），且有说明时自动展开', () => {
    const stopped = [{ ...entry('撰写报告内容', 'pending'), kind: 'report' as const }]
    render(
      <TracePanel
        entries={stopped}
        note="已停止显示，任务在后台继续执行，完成后会自动更新"
        running={false}
      />,
    )

    expect(screen.getByText('已停止')).toBeTruthy()
  })

  it('没有步骤也没有说明时不渲染（不占位）', () => {
    const { container } = render(<TracePanel entries={[]} running={false} />)

    expect(container.querySelector('.wb-trace')).toBeNull()
  })

  it('只有说明（如重连提示）时也渲染面板', () => {
    render(<TracePanel entries={[]} note="（重连）该会话有任务正在后台执行" running />)

    expect(screen.getByText(/重连/)).toBeTruthy()
  })
})

const BUILD_INTERRUPTED: TimelineEntry[] = [
  { ...entry('解析需求', 'success'), kind: 'agent' as const, timestamp: 1_700_000_000_000 },
  { ...entry('生成 SQL', 'error'), kind: 'sql' as const, timestamp: 1_700_000_004_000 },
]

  it('步骤显示这一步的结论（detail）——用户要的「思考内容」', () => {
    const withDetail: TimelineEntry[] = [
      {
        ...entry('识别意图', 'success'),
        kind: 'agent',
        detail: '意图=report（业务问题）',
      },
    ]
    render(<TracePanel entries={withDetail} running={false} />)
    fireEvent.click(screen.getByText('展开'))

    expect(screen.getByText('意图=report（业务问题）')).toBeTruthy()
  })

