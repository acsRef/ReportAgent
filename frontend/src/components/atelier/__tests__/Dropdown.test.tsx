import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Dropdown from '../Dropdown'

describe('Dropdown', () => {
  const items = [
    { key: '1', label: '选项一', onClick: vi.fn() },
    { key: '2', label: '选项二', onClick: vi.fn() },
    { key: '3', label: '选项三', onClick: vi.fn() },
  ]

  it('renders trigger and opens menu on click', async () => {
    render(<Dropdown items={items}><button>打开</button></Dropdown>)
    expect(screen.getByText('打开')).toBeInTheDocument()
    await userEvent.click(screen.getByText('打开'))
    expect(screen.getByText('选项一')).toBeInTheDocument()
    expect(screen.getByText('选项二')).toBeInTheDocument()
  })

  it('calls item onClick when menu item is clicked', async () => {
    render(<Dropdown items={items}><button>打开</button></Dropdown>)
    await userEvent.click(screen.getByText('打开'))
    await userEvent.click(screen.getByText('选项一'))
    expect(items[0].onClick).toHaveBeenCalled()
  })

  it('closes menu on item click', async () => {
    render(<Dropdown items={items}><button>打开</button></Dropdown>)
    await userEvent.click(screen.getByText('打开'))
    await userEvent.click(screen.getByText('选项一'))
    expect(screen.queryByText('选项二')).not.toBeInTheDocument()
  })

  it('closes menu on Escape', async () => {
    render(<Dropdown items={items}><button>打开</button></Dropdown>)
    await userEvent.click(screen.getByText('打开'))
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByText('选项一')).not.toBeInTheDocument()
  })

  it('renders divider for items with divider=true', async () => {
    const withDivider = [
      { key: '1', label: '选项一', onClick: vi.fn() },
      { key: 'd1', divider: true, onClick: vi.fn() },
      { key: '2', label: '选项二', onClick: vi.fn() },
    ]
    render(<Dropdown items={withDivider as any}><button>打开</button></Dropdown>)
    await userEvent.click(screen.getByText('打开'))
    const dividers = document.querySelectorAll('.atelier-popover__divider')
    expect(dividers.length).toBe(1)
  })
})
