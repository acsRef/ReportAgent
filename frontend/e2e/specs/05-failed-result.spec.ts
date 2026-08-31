import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

/**
 * 测试验收对象：P10 Report Runtime → `ReportVersion=FAILED` 落库 →
 * ReportPaper 历史 FAILED 版本视图（`.wb-finding` error band，含「执行失败」）。
 * 注：不是 `ErrorCard` 组件（项目无 ErrorCard 组件命名）。设计选择原因：
 * FAILED 是 report 的合法三态之一（SUCCESS/EMPTY/FAILED），统一在 ReportPaper
 * 内渲染，不为 FAILED 单独建组件——避免 ReportPaper 与 ErrorCard 双源真相漂移。
 */
test.describe('05-failed-result — SQL 耗尽修复预算 → ReportVersion=FAILED 落库 → ReportPaper 错误 band', () => {
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