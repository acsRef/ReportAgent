import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import TopBar from '../TopBar'

describe('TopBar', () => {
  it('renders brand text', () => {
    render(<TopBar brand="MyApp">content</TopBar>)
    expect(screen.getByText('MyApp')).toBeInTheDocument()
  })

  it('renders children', () => {
    render(<TopBar brand="MyApp"><button>btn</button></TopBar>)
    expect(screen.getByText('btn')).toBeInTheDocument()
  })

  it('has atelier-topbar class', () => {
    const { container } = render(<TopBar brand="MyApp" />)
    expect(container.firstElementChild?.className).toContain('atelier-topbar')
  })
})
