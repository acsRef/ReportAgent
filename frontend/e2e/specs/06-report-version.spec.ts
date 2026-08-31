import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('06-report-version — adjust 流 report 自动刷新选中新版', () => {
  test.beforeAll(async () => {
    await startContractBackend('report-version')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('报告 v1 后发送 adjust，自动切到 v2 并渲染新内容', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    // 第一轮：区域报告 v1
    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()
    await wb.expectReport()
    await wb.expectReportContains('华南')

    // 报告态下 composer 走 adjust（chatModeForPhase report_ready → adjust）
    await wb.sendQuery('按产品维度再看销售额')
    await wb.expectReportContains('茅台飞天53度')
    await expect(page.locator('.wb-report-version').first()).toContainText(/报告 v2/)
  })
})