import type { Page } from '@playwright/test'

/**
 * 通用轮询：直到 fn 为真或超时。Playwright 的 expect().toBeVisible({timeout})
 * 大多可替代，但等待「文本出现后消失 / 真 SSE trace 累积」这类动态条件时用一个显式
 * waitFor 更直接。
 */
export async function waitFor(
  page: Page,
  fn: () => boolean | Promise<boolean>,
  timeoutMs = 30_000,
  label = 'condition',
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let last = ''
  while (Date.now() < deadline) {
    try {
      if (await fn()) return
    } catch (err) {
      last = err instanceof Error ? err.message : String(err)
    }
    await page.locator('body').waitFor({ state: 'attached', timeout: 250 }).catch(() => {})
    await page.waitForTimeout(250)
  }
  throw new Error(`waitFor 超时（${label}, ${timeoutMs}ms） last=${last || 'never true'}`)
}

/** 等待 selector 出现且包含指定文本（用于 trace 帧累积等）。 */
export async function waitForText(
  page: Page,
  selector: string,
  text: string,
  timeoutMs = 30_000,
): Promise<void> {
  await waitFor(
    page,
    async () => (await page.locator(selector).allTextContents()).some((t) => t.includes(text)),
    timeoutMs,
    `${selector} 含 "${text}"`,
  )
}