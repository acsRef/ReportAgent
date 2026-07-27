import { describe, expect, it, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { ToastProvider } from '../Toast'
import { useToast } from '../useToast'

function TestConsumer() {
  const toast = useToast()
  return (
    <div>
      <button onClick={() => toast.success('成功!')}>success</button>
      <button onClick={() => toast.error('失败!')}>error</button>
      <button onClick={() => toast.warning('警告!')}>warning</button>
      <button onClick={() => toast.info('信息!')}>info</button>
    </div>
  )
}

describe('ToastProvider + useToast', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows toast in aria-live region on success', () => {
    render(
      <ToastProvider>
        <TestConsumer />
      </ToastProvider>,
    )
    act(() => {
      screen.getByText('success').click()
    })
    const region = document.querySelector('.atelier-toast-root')
    expect(region).toBeInTheDocument()
    expect(region!.getAttribute('aria-live')).toBe('polite')
    expect(region!.textContent).toContain('成功!')
  })

  it('shows error toast', () => {
    render(
      <ToastProvider>
        <TestConsumer />
      </ToastProvider>,
    )
    act(() => {
      screen.getByText('error').click()
    })
    expect(document.querySelector('.atelier-toast-root')!.textContent).toContain('失败!')
  })

  it('shows warning toast', () => {
    render(
      <ToastProvider>
        <TestConsumer />
      </ToastProvider>,
    )
    act(() => {
      screen.getByText('warning').click()
    })
    expect(document.querySelector('.atelier-toast-root')!.textContent).toContain('警告!')
  })

  it('shows info toast', () => {
    render(
      <ToastProvider>
        <TestConsumer />
      </ToastProvider>,
    )
    act(() => {
      screen.getByText('info').click()
    })
    expect(document.querySelector('.atelier-toast-root')!.textContent).toContain('信息!')
  })

  it('auto-dismisses toast after 3 seconds', () => {
    render(
      <ToastProvider>
        <TestConsumer />
      </ToastProvider>,
    )
    act(() => {
      screen.getByText('success').click()
    })
    expect(document.querySelector('.atelier-toast-root')!.childElementCount).toBe(1)
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(document.querySelector('.atelier-toast-root')!.childElementCount).toBe(0)
  })

  it('throws when useToast is used outside provider', () => {
    function Bad() {
      useToast()
      return null
    }
    expect(() => render(<Bad />)).toThrow('useToast must be used within ToastProvider')
  })
})
