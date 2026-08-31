import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('07-background-execution — 停止 → 后台跑完 → 5s 轮询通知', () => {
  test.beforeAll(async () => {
    await startContractBackend('background-execution')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('生成中点击停止，任务继续后台跑完，轮询出 toast', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年华南区销售额')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()

    // 停止生成（fixture 用 pg_sleep(3) 拉长 generating 窗口，确保按钮可点）
    const stopBtn = page.getByRole('button', { name: '停止生成' })
    await expect(stopBtn).toBeVisible({ timeout: 20_000 })
    await stopBtn.click()

    // 前端停止显示 + 5s 轮询 → 后台完成 → toast 通知
    await expect(page.locator('body')).toContainText('报告已在后台生成，可查看', {
      timeout: 40_000,
    })
  })
})