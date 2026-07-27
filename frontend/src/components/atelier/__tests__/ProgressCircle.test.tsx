import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import ProgressCircle from '../ProgressCircle'

describe('ProgressCircle', () => {
  it('renders percentage text', () => {
    render(<ProgressCircle percent={65} />)
    expect(screen.getByText('65%')).toBeInTheDocument()
  })

  it('renders SVG with correct viewBox', () => {
    const { container } = render(<ProgressCircle percent={50} />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    expect(svg?.getAttribute('viewBox')).toBe('0 0 64 64')
  })

  it('clamps percent between 0 and 100', () => {
    const { rerender } = render(<ProgressCircle percent={120} />)
    expect(screen.getByText('100%')).toBeInTheDocument()
    rerender(<ProgressCircle percent={-10} />)
    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('has atelier-progress-circle class', () => {
    const { container } = render(<ProgressCircle percent={0} />)
    expect(container.firstElementChild?.className).toContain('atelier-progress-circle')
  })
})
