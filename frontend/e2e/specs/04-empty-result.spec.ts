import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('04-empty-result — 合法零行 → EMPTY band 渲染', () => {
  test.beforeAll(async () => {
    await startContractBackend('empty-result')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('零行查询走 EMPTY，不渲染伪造报告', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()

    await wb.expectEmptyBand()
    await expect(page.locator('.wb-empty-text')).toContainText('未找到匹配记录')
    // EMPTY 是真实状态，不会被伪装成成功报告
    await expect(page.locator('.wb-report-version').first()).not.toContainText('执行成功', { timeout: 3000 })
  })
})