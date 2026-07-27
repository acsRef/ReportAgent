import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Tag from '../Tag'

describe('Tag', () => {
  it('renders with default tone', () => {
    render(<Tag>Default</Tag>)
    const el = screen.getByText('Default')
    expect(el.className).toContain('atelier-tag--default')
  })

  it('renders all tone classes', () => {
    const tones = ['teal', 'amber', 'red', 'green', 'ink', 'default'] as const
    for (const tone of tones) {
      render(<Tag tone={tone}>{tone}</Tag>)
      expect(screen.getByText(tone).className).toContain(`atelier-tag--${tone}`)
    }
  })

  it('renders children', () => {
    render(<Tag tone="teal">销售</Tag>)
    expect(screen.getByText('销售')).toBeInTheDocument()
  })

  it('merges custom className', () => {
    render(<Tag className="extra">Tag</Tag>)
    expect(screen.getByText('Tag').className).toContain('extra')
  })
})
