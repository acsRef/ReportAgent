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
  { ...entry('安全检查', 'success'), kind: 'agent', timestamp: 1_700_000_000_000 },
  { ...entry('识别意图', 'success'), kind: 'agent', timestamp: 1_700_000_001_200 },
  { ...entry('解析需求', 'running'), kind: 'agent', timestamp: 1_700_000_009_000 },
]

const DONE: TimelineEntry[] = [
  { ...entry('解析需求', 'success'), kind: 'agent', timestamp: 1_700_000_000_000 },
  { ...entry('口径预览', 'success'), kind: 'tool', timestamp: 1_700_000_002_500 },
  { ...entry('生成 SQL', 'success'), kind: 'sql', timestamp: 1_700_000_006_000 },
]

describe('TracePanel — 一行摘要 + 展开看步骤（2026-11-04 返工）', () => {
  it('运行中：一行「正在思考 · 当前步骤」，默认不铺开步骤', () => {
    render(<TracePanel entries={RUNNING} running />)

    expect(screen.getByText('正在思考 · 解析需求')).toBeTruthy()
    expect(screen.queryByText('安全检查')).toBeNull()
  })

  it('结束后：一行「思考了 N 步 · 耗时」', () => {
    render(<TracePanel entries={DONE} running={false} />)

    expect(screen.getByText('思考了 3 步 · 6.0s')).toBeTruthy()
  })

  it('展开后才显示每一步（含状态）', () => {
    render(<TracePanel entries={DONE} running={false} />)
    fireEvent.click(screen.getByText('思考了 3 步 · 6.0s'))

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
    fireEvent.click(screen.getByText(/思考了 1 步/))

    expect(screen.getByText('意图=report（业务问题）')).toBeTruthy()
  })

  it('失败：一行点名中断在哪一步', () => {
    const failed: TimelineEntry[] = [
      ...RUNNING.slice(0, 2),
      { ...entry('解析需求', 'error'), kind: 'agent' },
    ]
    render(<TracePanel entries={failed} running={false} />)

    expect(screen.getByText('思考中断于「解析需求」')).toBeTruthy()
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
