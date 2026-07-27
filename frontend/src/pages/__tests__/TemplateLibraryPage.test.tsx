import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TemplateLibraryPage from '../TemplateLibraryPage'
import { ToastProvider } from '../../components/atelier/Toast'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  localStorage.setItem('ragent_auth', JSON.stringify({ state: { token: 'tok' } }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => jsonResponse({ templates: [] })),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <TemplateLibraryPage />
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('TemplateLibraryPage — antd-free create modal', () => {
  it('empty name shows 请输入模板名称 and never POSTs', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('新建模板'))
    fireEvent.click(screen.getByText('创建'))
    expect(await screen.findByText('请输入模板名称')).toBeTruthy()
    const posts = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
    )
    expect(posts).toHaveLength(0)
  })

  it('name longer than 128 chars shows 名称不能超过 128 字符', async () => {
    renderPage()
    fireEvent.click(await screen.findByText('新建模板'))
    fireEvent.change(screen.getByPlaceholderText('例如：华东月销售分析'), {
      target: { value: 'x'.repeat(129) },
    })
    fireEvent.click(screen.getByText('创建'))
    expect(await screen.findByText('名称不能超过 128 字符')).toBeTruthy()
  })

  it('valid name POSTs and toasts 已创建模板「…」', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (init?.method === 'POST' && url.includes('/templates')) {
          return jsonResponse({
            template: {
              id: 7,
              name: '月度复盘',
              description: '',
              requirement_payload: { id: 'x', version: 1 },
              created_at: '2026-07-27',
              updated_at: '2026-07-27',
            },
          })
        }
        return jsonResponse({ templates: [] })
      },
    )
    renderPage()
    fireEvent.click(await screen.findByText('新建模板'))
    fireEvent.change(screen.getByPlaceholderText('例如：华东月销售分析'), {
      target: { value: '月度复盘' },
    })
    fireEvent.click(screen.getByText('创建'))
    expect(
      await screen.findByText('已创建模板「月度复盘」', {}, { timeout: 3000 }),
    ).toBeTruthy()
  })
})
