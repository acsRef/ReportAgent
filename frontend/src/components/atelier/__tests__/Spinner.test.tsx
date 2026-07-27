import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Spinner from '../Spinner'

describe('Spinner', () => {
  it('renders with default size', () => {
    render(<Spinner />)
    const el = screen.getByRole('status')
    expect(el.className).toContain('atelier-spinner')
    expect(el.className).not.toContain('atelier-spinner--')
  })

  it('renders with sm and lg sizes', () => {
    const { unmount } = render(<Spinner size="sm" />)
    expect(screen.getByRole('status').className).toContain('atelier-spinner--sm')
    unmount()
    render(<Spinner size="lg" />)
    expect(screen.getByRole('status').className).toContain('atelier-spinner--lg')
  })

  it('renders with label', () => {
    render(<Spinner label="加载中" />)
    expect(screen.getByText('加载中')).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('has accessible aria-label', () => {
    render(<Spinner label="loading" />)
    expect(screen.getByRole('status').getAttribute('aria-label')).toBe('loading')
  })
})
