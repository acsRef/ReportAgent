import { test, expect } from '@playwright/test'
import { startFullBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

const fullGate = !process.env.REPORTAGENT_E2E
test.skip(fullGate, 'Full E2E requires REPORTAGENT_E2E=1（CI 自动 skip）')

test.describe('12-empty-result — 真实 LLM + 真实 MCP：seed 仅 2024，2025 查询 → 报告 band', () => {
  test.beforeAll(async () => {
    await startFullBackend()
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  /**
   * 真 LLM 在 scope/metric/assumptions 留空时补齐按钮可能永 disabled——
   * 每组选首个 label（覆盖 RadioGroup/CheckboxGroup）+ assumptions 全部接受。
   */
  async function fillMissingIfAny(page: import('@playwright/test').Page): Promise<void> {
    const status = await page.locator('.wb-req-status').first().innerText()
    if (!/需要补充/.test(status)) return
    const groups = page.locator('.wb-option-group')
    const n = await groups.count()
    for (let i = 0; i < n; i++) {
      const g = groups.nth(i)
      await g.locator('label.atelier-radio-pill, label.atelier-checkbox').first().click()
    }
    const acceptBtns = page.locator('.wb-assumption').locator('.wb-mini-btn', { hasText: '接受' })
    const m = await acceptBtns.count()
    for (let i = 0; i < m; i++) await acceptBtns.nth(i).click()
    const reviewBtn = page.getByRole('button', { name: '补充完成，查看确认' })
    await expect(reviewBtn).toBeEnabled({ timeout: 10_000 })
    await reviewBtn.click()
    await expect(page.locator('.wb-req-status').first()).toContainText('信息完整 · 待确认')
  }

  // 真 LLM 解释「2025」时可能写成 BETWEEN 或 >= 而命中 2024 数据——
  // 故断言放宽到「报告渲染 + 非 FAILED」。EMPTY band 由 spec 04（Contract mock）确定性钉。
  test('2025 查询 → 报告渲染成功（不伪失败）', async ({ page }) => {
    test.setTimeout(180_000)

    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2025年各区域销售额')
    await expect(page.locator('.wb-requirement-card')).toBeVisible({ timeout: 90_000 })
    await fillMissingIfAny(page)

    await wb.confirmRequirement()

    await wb.expectReport()
    // 没有 error/failed 卡：FULL LLM 不伪成功
    await expect(page.locator('.wb-progress-card.failed')).toHaveCount(0)
    await expect(page.locator('.wb-report-paper')).toBeVisible()
  })
})