import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('02-clarification — 缺字段 → PATCH 补全 → 确认 → 报告', () => {
  test.beforeAll(async () => {
    await startContractBackend('clarification')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('缺指标字段时渲染缺失组，补全后确认生成报告', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('帮我分析销售额')
    await wb.expectRequirementCard()

    // 卡含缺指标字段（后端 fixture 返回 missing_fields=["metric"]）
    await expect(page.locator('.wb-option-group').filter({ hasText: '指标' })).toBeVisible()

    // 勾选「销售额」
    await page
      .locator('.wb-option-group', { hasText: '指标' })
      .locator('label.atelier-checkbox', { hasText: '销售额' })
      .click()

    // 「补充完成，查看确认」→ 本地置 complete → 卡变完整态
    await wb.clickPrimary('补充完成，查看确认')
    await expect(page.locator('.wb-req-status')).toContainText('信息完整 · 待确认')

    // 再点「确认并生成报告」→ PATCH → 服务端归一化 → 自动确认
    await wb.confirmRequirement()

    await wb.expectReport()
    await wb.expectReportContains('华南')
  })
})