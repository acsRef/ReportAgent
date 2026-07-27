import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RadioGroup from '../RadioGroup'

describe('RadioGroup', () => {
  const options = [
    { value: 'a', label: '选项A' },
    { value: 'b', label: '选项B' },
    { value: 'c', label: '选项C' },
  ]

  it('renders all options as radio buttons', () => {
    render(<RadioGroup options={options} value={null} onChange={() => {}} />)
    expect(screen.getByText('选项A')).toBeInTheDocument()
    expect(screen.getByText('选项B')).toBeInTheDocument()
    expect(screen.getByText('选项C')).toBeInTheDocument()
  })

  it('calls onChange with selected value on click', async () => {
    const onChange = vi.fn()
    render(<RadioGroup options={options} value={null} onChange={onChange} />)
    await userEvent.click(screen.getByText('选项B'))
    expect(onChange).toHaveBeenCalledWith('b')
  })

  it('shows selected option with input:checked', () => {
    render(<RadioGroup options={options} value="b" onChange={() => {}} />)
    const radios = document.querySelectorAll<HTMLInputElement>('input[type="radio"]')
    expect(radios[1].checked).toBe(true)
    expect(radios[0].checked).toBe(false)
    expect(radios[2].checked).toBe(false)
  })

  it('renders with kind=pill class', () => {
    render(<RadioGroup kind="pill" options={options} value={null} onChange={() => {}} />)
    const labels = document.querySelectorAll<HTMLLabelElement>('.atelier-radio-pill')
    expect(labels.length).toBe(3)
  })
})
