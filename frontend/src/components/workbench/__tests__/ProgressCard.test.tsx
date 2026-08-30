import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import ProgressCard from '../ProgressCard'

describe('ProgressCard', () => {
  it('renders liveDetail (P11 trace 文案) when provided', () => {
    render(<ProgressCard stageIndex={2} liveDetail="正在生成 SQL…" />)
    expect(screen.getByText('正在生成 SQL…')).toBeTruthy()
  })

  it('falls back to default copy when no liveDetail', () => {
    const { container } = render(<ProgressCard adjusting stageIndex={1} />)
    expect(container.textContent).toContain('原报告仍可回看')
  })

  it('failed card shows FAILED + no stop button', () => {
    render(<ProgressCard stageIndex={0} failed onStop={vi.fn()} />)
    expect(screen.getByText('FAILED')).toBeTruthy()
    expect(screen.queryByText('停止生成')).toBeNull()
  })
})