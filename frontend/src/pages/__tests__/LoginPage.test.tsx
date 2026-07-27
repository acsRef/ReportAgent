import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { ToastProvider } from '../../components/atelier/Toast'
import LoginPage from '../LoginPage'
import * as api from '../../api/api'

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

vi.mock('../../api/api', () => ({
  loginAPI: vi.fn(),
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderPage() {
  return render(
    <BrowserRouter>
      <ToastProvider>
        <LoginPage />
      </ToastProvider>
    </BrowserRouter>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders login form with default credentials', () => {
    renderPage()
    expect(screen.getByText('登录')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('admin')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('••••••••')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '进入工作台' })).toBeInTheDocument()
  })

  it('shows success toast and navigates on valid login', async () => {
    vi.mocked(api.loginAPI).mockResolvedValueOnce({
      access_token: 'test-token',
      user_id: 1,
      username: 'admin',
    })
    renderPage()
    await userEvent.clear(screen.getByPlaceholderText('admin'))
    await userEvent.type(screen.getByPlaceholderText('admin'), 'admin')
    await userEvent.clear(screen.getByPlaceholderText('••••••••'))
    await userEvent.type(screen.getByPlaceholderText('••••••••'), 'admin123')
    await userEvent.click(screen.getByRole('button', { name: '进入工作台' }))
    await waitFor(() => {
      expect(api.loginAPI).toHaveBeenCalledWith('admin', 'admin123')
    })
    expect(mockNavigate).toHaveBeenCalledWith('/')
    const toastRoot = document.querySelector('.atelier-toast-root')
    expect(toastRoot?.textContent).toContain('登录成功')
  })

  it('shows error toast on login failure', async () => {
    vi.mocked(api.loginAPI).mockRejectedValueOnce(new Error('密码错误'))
    renderPage()
    await userEvent.clear(screen.getByPlaceholderText('admin'))
    await userEvent.type(screen.getByPlaceholderText('admin'), 'admin')
    await userEvent.clear(screen.getByPlaceholderText('••••••••'))
    await userEvent.type(screen.getByPlaceholderText('••••••••'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: '进入工作台' }))
    await waitFor(() => {
      expect(api.loginAPI).toHaveBeenCalled()
    })
    const toastRoot = document.querySelector('.atelier-toast-root')
    expect(toastRoot?.textContent).toContain('登录失败')
  })

  it('shows validation hint when username is empty', async () => {
    renderPage()
    await userEvent.clear(screen.getByPlaceholderText('admin'))
    await userEvent.click(screen.getByRole('button', { name: '进入工作台' }))
    await waitFor(() => {
      expect(screen.getByText('请输入用户名')).toBeInTheDocument()
    })
  })
})
