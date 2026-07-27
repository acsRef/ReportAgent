import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Empty from '../Empty'

describe('Empty', () => {
  it('renders with title and description', () => {
    render(<Empty title="暂无数据" description="请重新查询" />)
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
    expect(screen.getByText('请重新查询')).toBeInTheDocument()
  })

  it('renders icon', () => {
    render(<Empty icon={<span>📭</span>} />)
    expect(screen.getByText('📭')).toBeInTheDocument()
  })

  it('renders action slot', () => {
    render(<Empty action={<button>重试</button>} />)
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('renders without any props', () => {
    const { container } = render(<Empty />)
    expect(container.querySelector('.atelier-empty')).toBeInTheDocument()
  })
})
