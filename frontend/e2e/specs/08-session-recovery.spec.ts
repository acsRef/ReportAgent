import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('08-session-recovery — 断网重进恢复真实 phase + 版本', () => {
  test.beforeAll(async () => {
    await startContractBackend('happy-path')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('报告完成后重进会话，phase/报告版本恢复', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)

    // 创建并完成一个报告会话
    await wb.open()
    await wb.startNewSession()
    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()
    await wb.expectReport()

    // 模拟断网重进：整页刷新
    await page.reload()

    // 会话 rail 点选该会话 → 恢复 phase 与报告版本
    await wb.open()
    await page.locator('.wb-session-main').first().click()
    await wb.expectReport()
    await wb.expectReportContains('华南')
  })
})