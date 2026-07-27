import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TextArea from '../TextArea'

describe('TextArea', () => {
  it('renders and accepts input', async () => {
    const onChange = vi.fn()
    render(<TextArea value="" onChange={onChange} placeholder="描述" />)
    const ta = screen.getByPlaceholderText('描述')
    expect(ta).toBeInTheDocument()
    await userEvent.type(ta, 'x')
    expect(onChange).toHaveBeenCalled()
  })

  it('renders error class', () => {
    render(<TextArea error placeholder="err" />)
    expect(screen.getByPlaceholderText('err').className).toContain('is-error')
  })

  it('applies disabled attribute', () => {
    render(<TextArea disabled placeholder="d" />)
    expect(screen.getByPlaceholderText('d')).toBeDisabled()
  })
})
