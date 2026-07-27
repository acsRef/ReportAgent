import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{}', { status: 200 })),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('App routing smoke', () => {
  it('unauthenticated visit lands on the login page', async () => {
    render(<App />)
    // login card renders (AuthGuard redirects / → /login)
    expect(await screen.findByText('进入工作台')).toBeTruthy()
    expect(screen.getByText('默认账号 · admin / admin123')).toBeTruthy()
  })
})
