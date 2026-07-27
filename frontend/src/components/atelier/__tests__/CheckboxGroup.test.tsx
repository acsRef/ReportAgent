import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CheckboxGroup from '../CheckboxGroup'

describe('CheckboxGroup', () => {
  const options = [
    { value: 'a', label: '选项A' },
    { value: 'b', label: '选项B' },
  ]

  it('renders all options as checkboxes', () => {
    render(<CheckboxGroup options={options} value={[]} onChange={() => {}} />)
    expect(screen.getByText('选项A')).toBeInTheDocument()
    expect(screen.getByText('选项B')).toBeInTheDocument()
  })

  it('calls onChange with added value on check', async () => {
    const onChange = vi.fn()
    render(<CheckboxGroup options={options} value={[]} onChange={onChange} />)
    await userEvent.click(screen.getByText('选项A'))
    expect(onChange).toHaveBeenCalledWith(['a'])
  })

  it('calls onChange with removed value on uncheck', async () => {
    const onChange = vi.fn()
    render(<CheckboxGroup options={options} value={['a', 'b']} onChange={onChange} />)
    await userEvent.click(screen.getByText('选项A'))
    expect(onChange).toHaveBeenCalledWith(['b'])
  })

  it('shows checkboxes with correct checked state', () => {
    render(<CheckboxGroup options={options} value={['a']} onChange={() => {}} />)
    const cbs = document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')
    expect(cbs[0].checked).toBe(true)
    expect(cbs[1].checked).toBe(false)
  })
})
