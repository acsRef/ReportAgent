import { test } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('03-retry — SQL 首轮失败 → repair 成功 → 报告', () => {
  test.beforeAll(async () => {
    await startContractBackend('retry')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('坏 SQL 触发 repair，第二版 fixture 成功后出报告', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()

    // 第一次 SQL 坏 → 内部 repair → 第二次好 SQL → 最终 report_ready
    await wb.expectReport()
    await wb.expectReportContains('华东')
  })
})