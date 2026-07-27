import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TextField from '../TextField'

describe('TextField', () => {
  it('renders and accepts controlled input', async () => {
    const onChange = vi.fn()
    render(<TextField value="" onChange={onChange} placeholder="输入" />)
    const input = screen.getByPlaceholderText('输入')
    expect(input).toBeInTheDocument()
    await userEvent.type(input, 'a')
    expect(onChange).toHaveBeenCalled()
  })

  it('renders error class', () => {
    render(<TextField error placeholder="err" />)
    expect(screen.getByPlaceholderText('err').className).toContain('is-error')
  })

  it('renders password type', () => {
    render(<TextField type="password" placeholder="pwd" />)
    expect(screen.getByPlaceholderText('pwd')).toHaveAttribute('type', 'password')
  })

  it('applies disabled attribute', () => {
    render(<TextField disabled placeholder="d" />)
    expect(screen.getByPlaceholderText('d')).toBeDisabled()
  })

  it('merges custom className', () => {
    render(<TextField className="my-field" placeholder="x" />)
    expect(screen.getByPlaceholderText('x').className).toContain('my-field')
  })
})
