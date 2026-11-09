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

  // 真 LLM 的缺项条数不确定：`resolveRequirementIfAsked()` 按「一次一问」逐题选首个选项，
  // 直到出现确认条为止（2026-11-09 起替代旧的整卡 checkbox/「补充完成」补全逻辑）。
  // 真 LLM 解释「2025」时可能写成 BETWEEN 或 >= 而命中 2024 数据——
  // 故断言放宽到「报告渲染 + 非 FAILED」。EMPTY band 由 spec 04（Contract mock）确定性钉。
  test('2025 查询 → 报告渲染成功（不伪失败）', async ({ page }) => {
    test.setTimeout(180_000)

    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2025年各区域销售额')
    await wb.expectRequirementCard()
    await wb.resolveRequirementIfAsked()

    await wb.confirmRequirement()

    await wb.expectReport()
    // 没有 error/failed 卡：FULL LLM 不伪成功
    await expect(page.locator('.wb-turn-error')).toHaveCount(0)
    await expect(page.locator('.wb-report-paper')).toBeVisible()
  })
})
