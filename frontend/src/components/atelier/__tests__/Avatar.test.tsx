import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Avatar from '../Avatar'

describe('Avatar', () => {
  it('renders with default size', () => {
    render(<Avatar>U</Avatar>)
    const el = screen.getByText('U')
    expect(el.className).toContain('atelier-avatar')
    expect(el.className).not.toContain('--sm')
    expect(el.className).not.toContain('--lg')
  })

  it('renders sm size', () => {
    render(<Avatar size="sm">U</Avatar>)
    expect(screen.getByText('U').className).toContain('atelier-avatar--sm')
  })

  it('renders lg size', () => {
    render(<Avatar size="lg">U</Avatar>)
    expect(screen.getByText('U').className).toContain('atelier-avatar--lg')
  })

  it('uses className prop', () => {
    render(<Avatar className="my-class">U</Avatar>)
    const el = screen.getByText('U')
    expect(el.className).toContain('my-class')
    expect(el.className).toContain('atelier-avatar')
  })
})
