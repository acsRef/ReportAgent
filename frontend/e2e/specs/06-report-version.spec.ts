import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('06-report-version — 追问 = supplement 重新出卡 → 确认 → 渲染 v2', () => {
  test.beforeAll(async () => {
    await startContractBackend('report-version')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('报告 v1 后追问「按产品维度」→ 新卡 → 确认 → 自动渲染 v2', async ({ page }) => {
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

    // 2026-11-02 起 `report_ready` 的追问走 **supplement**（回需求层重新解析），
    // 不再是 `adjust` 直接重跑旧卡（否则「详细讲一下七月份」只会拿到同一张卡的第二份报告）。
    // 先等上一轮真结束：报告 shell 先挂、phase 后落，抢跑会走到别的分支。
    await wb.waitIdle()

    // 第二轮：追问 → 需求卡（产品维度）→ 确认 → v2
    await wb.sendQuery('按产品维度再看销售额')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()
    // v2 的标题来自**追问那一轮**的需求卡（旧版会沿用上一轮标题）
    await wb.expectReportContains('2024年各产品销售额')
    await expect(page.locator('.wb-report-version').first()).toContainText(/报告 v2/)
  })
})
