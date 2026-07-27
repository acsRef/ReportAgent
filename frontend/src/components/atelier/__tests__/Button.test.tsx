import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Button from '../Button'

describe('Button', () => {
  it('renders with default variant and responds to click', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Click me</Button>)
    const btn = screen.getByRole('button', { name: 'Click me' })
    expect(btn.className).toContain('atelier-btn')
    expect(btn.className).not.toContain('atelier-btn--primary')
    await userEvent.click(btn)
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('renders primary variant class', () => {
    render(<Button variant="primary">Primary</Button>)
    const btn = screen.getByRole('button', { name: 'Primary' })
    expect(btn.className).toContain('atelier-btn--primary')
  })

  it('renders quiet variant class', () => {
    render(<Button variant="quiet">Quiet</Button>)
    expect(screen.getByRole('button', { name: 'Quiet' }).className).toContain('atelier-btn--quiet')
  })

  it('renders danger variant class', () => {
    render(<Button variant="danger">Danger</Button>)
    expect(screen.getByRole('button', { name: 'Danger' }).className).toContain('atelier-btn--danger')
  })

  it('renders size classes', () => {
    render(<Button size="sm">Small</Button>)
    expect(screen.getByRole('button', { name: 'Small' }).className).toContain('atelier-btn--sm')
    render(<Button size="lg">Large</Button>)
    expect(screen.getByRole('button', { name: 'Large' }).className).toContain('atelier-btn--lg')
  })

  it('renders block class', () => {
    render(<Button block>Block</Button>)
    expect(screen.getByRole('button', { name: 'Block' }).className).toContain('atelier-btn--block')
  })

  it('sets aria-busy when loading', () => {
    render(<Button loading>Loading</Button>)
    const btn = screen.getByRole('button', { name: 'Loading' })
    expect(btn.getAttribute('aria-busy')).toBe('true')
  })

  it('applies disabled attribute', () => {
    render(<Button disabled>Disabled</Button>)
    expect(screen.getByRole('button', { name: 'Disabled' })).toBeDisabled()
  })

  it('merges custom className', () => {
    render(<Button className="my-class">Custom</Button>)
    expect(screen.getByRole('button', { name: 'Custom' }).className).toContain('my-class')
  })
})
