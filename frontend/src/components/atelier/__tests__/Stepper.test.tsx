import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Stepper from '../Stepper'

describe('Stepper', () => {
  it('renders current value', () => {
    render(<Stepper value={3} onChange={() => {}} min={1} max={10} />)
    const input = screen.getByRole('textbox') as HTMLInputElement
    expect(input.value).toBe('3')
  })

  it('increments on plus button', async () => {
    const onChange = vi.fn()
    render(<Stepper value={3} onChange={onChange} min={1} max={10} />)
    const buttons = screen.getAllByRole('button')
    await userEvent.click(buttons[1])
    expect(onChange).toHaveBeenCalledWith(4)
  })

  it('decrements on minus button', async () => {
    const onChange = vi.fn()
    render(<Stepper value={3} onChange={onChange} min={1} max={10} />)
    const buttons = screen.getAllByRole('button')
    await userEvent.click(buttons[0])
    expect(onChange).toHaveBeenCalledWith(2)
  })

  it('does not decrement below min', async () => {
    const onChange = vi.fn()
    render(<Stepper value={1} onChange={onChange} min={1} max={10} />)
    const buttons = screen.getAllByRole('button')
    await userEvent.click(buttons[0])
    expect(onChange).not.toHaveBeenCalled()
  })

  it('does not increment above max', async () => {
    const onChange = vi.fn()
    render(<Stepper value={10} onChange={onChange} min={1} max={10} />)
    const buttons = screen.getAllByRole('button')
    await userEvent.click(buttons[1])
    expect(onChange).not.toHaveBeenCalled()
  })
})
