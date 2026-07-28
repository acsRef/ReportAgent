import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ErrorCard from '../ErrorCard'

describe('ErrorCard', () => {
  it('uses generic title when kind is missing (legacy callers)', () => {
    render(<ErrorCard message="boom" onRetry={() => {}} />)
    expect(screen.getByText('执行分析时发生错误')).toBeTruthy()
    expect(screen.getByText('boom')).toBeTruthy()
  })

  it('maps each kind to a distinct title so users see the specific failure', () => {
    const cases: Array<[string, string]> = [
      ['timeout',    '查询超时'],
      ['connection', '数据库连接失败'],
      ['permission', '权限不足'],
      ['syntax',     'SQL 语法错误'],
      ['object',     '查询对象不存在'],
      ['other',      '查询执行失败'],
    ]
    for (const [kind, expectedTitle] of cases) {
      const { unmount } = render(
        <ErrorCard message="m" kind={kind as any} onRetry={() => {}} />,
      )
      expect(screen.getByText(expectedTitle)).toBeTruthy()
      unmount()
    }
  })

  it('shows the tried SQL inside a collapsible disclosure when provided', () => {
    const { container } = render(
      <ErrorCard message="x" kind="timeout" sql="SELECT 1 FROM fact_sales" onRetry={() => {}} />,
    )
    const details = container.querySelector('details.wb-progress-sql')
    expect(details).toBeTruthy()
    // Collapsed by default — content not visible until opened.
    expect((details as HTMLDetailsElement).open).toBe(false)
    fireEvent.click(screen.getByText('查看尝试的 SQL'))
    expect((details as HTMLDetailsElement).open).toBe(true)
    expect(screen.getByText('SELECT 1 FROM fact_sales')).toBeTruthy()
  })

  it('hides the SQL disclosure entirely when sql prop is missing', () => {
    const { container } = render(<ErrorCard message="x" kind="timeout" onRetry={() => {}} />)
    expect(container.querySelector('details.wb-progress-sql')).toBeNull()
  })

  it('retry button is wired to onRetry', () => {
    const onRetry = vi.fn()
    render(<ErrorCard message="x" onRetry={onRetry} />)
    fireEvent.click(screen.getByText('重试当前任务'))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('retry button is disabled while retrying', () => {
    const onRetry = vi.fn()
    render(<ErrorCard message="x" onRetry={onRetry} retrying />)
    const btn = screen.getByText('重试当前任务') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('falls back to a generic message when none provided', () => {
    render(<ErrorCard onRetry={() => {}} />)
    expect(screen.getByText('查询未能返回数据。')).toBeTruthy()
  })
})