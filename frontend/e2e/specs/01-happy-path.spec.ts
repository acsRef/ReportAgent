import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('01-happy-path — 需求 → 确认 → 报告（Contract mock LLM + real PG）', () => {
  test.beforeAll(async () => {
    await startContractBackend('happy-path')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('mock fixture 驱动完整主链，报告渲染真实区域数据', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额排名')

    // /chat：需求解析 → 确认条（complete）。A4 之后先看「那句话」，参数卡按需展开。
    await wb.expectRequirementCard()
    await wb.expectRequirementAsked('销售额')
    await wb.openRequirementDetails()
    await expect(page.locator('.wb-req-status').last()).toContainText('信息完整 · 待确认')
    await wb.closeRequirementDetails()

    // /confirm：生成报告
    await wb.confirmRequirement()
    await wb.expectReport()
    await wb.expectReportContains('华南')
    await wb.expectReportContains('华东')
  })
})