import type { Page } from '@playwright/test'
import { BACKEND_URL, FRONTEND_URL } from './env'

/**
 * 登录（真实后端点 /auth/login）+ 把 token 注入 localStorage。
 * 与 .e2elogs/ui_pagination.mjs 注入写法一致：key = `ragent_auth`，形如
 * `{state:{token, user}}`（authStore zustand persist 格式）。
 */
export async function auth(page: Page): Promise<string> {
  const resp = await page.request.post(`${BACKEND_URL}/api/v1/auth/login`, {
    data: { username: 'admin', password: 'admin123' },
  })
  if (!resp.ok()) {
    throw new Error(`login failed ${resp.status()}: ${await resp.text()}`)
  }
  const body = (await resp.json()) as { access_token: string }
  if (!body.access_token) {
    throw new Error(`login 响应缺少 access_token: ${JSON.stringify(body)}`)
  }
  await page.goto(`${FRONTEND_URL}/`)
  await page.evaluate(
    (token) => {
      localStorage.setItem(
        'ragent_auth',
        JSON.stringify({ state: { token, user: { id: 1, username: 'admin' } } }),
      )
    },
    body.access_token,
  )
  await page.goto(`${FRONTEND_URL}/`)
  return body.access_token
}