import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('05-failed-result — SQL 耗尽修复预算 → ErrorCard 分类文案', () => {
  test.beforeAll(async () => {
    await startContractBackend('failed-result')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('坏 SQL 连败至预算耗尽，前端渲染 error 卡（不伪成功）', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()

    // 坏 SQL 连败至预算耗尽 → 后端 Persist FAILED 版本 → ReportPaper 错误 band
    const errBand = page.locator('.wb-finding').first()
    await expect(errBand).toBeVisible({ timeout: 90_000 })
    await expect(errBand).toContainText('执行失败')
  })
})