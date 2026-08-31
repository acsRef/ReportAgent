import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('09-memory-multiturn — 多轮会话偏好延续（Contract 近似）', () => {
  test.beforeAll(async () => {
    await startContractBackend('memory-multiturn')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('第二轮补充「只看华东」→ 需求卡收窄 → 报告仅华东', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    // 第一轮：需求卡（awaiting_confirm）
    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()

    // 第二轮 supplement：补充偏好「只看华东」
    await wb.sendQuery('只看华东')
    await wb.expectRequirementCard()
    await expect(page.locator('.wb-req-summary')).toContainText('华东')

    await wb.confirmRequirement()
    await wb.expectReport()
    await wb.expectReportContains('华东')
    // 偏好收窄生效：不含华南
    await expect(page.locator('.wb-report-shell').first()).not.toContainText('华南')
  })
})