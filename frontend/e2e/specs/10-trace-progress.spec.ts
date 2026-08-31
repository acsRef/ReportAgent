import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('10-trace-progress — 执行期 ProgressCard 显示真实 trace 文案', () => {
  test.beforeAll(async () => {
    await startContractBackend('happy-path')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('confirm 期间 .wb-progress-detail 由 trace 帧驱动（含真实节点名，非假定时器）', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    // 50ms 间隔快照 .wb-progress-detail 文本，确认期累积
    const captured: string[] = []
    const poll = setInterval(async () => {
      try {
        const texts = await page.locator('.wb-progress-detail').allTextContents()
        captured.push(...texts.filter(Boolean))
      } catch {
        /* locator 已卸载 */
      }
    }, 50)

    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()
    await wb.expectReport()
    clearInterval(poll)

    const joined = captured.join('|')
    // P11：live detail 由 trace 帧渲染，包含真实节点名——`正在生成 SQL…` 来自生成 SQL 节点
    expect(joined, 'ProgressCard live 文本含真实 trace 节点名').toMatch(
      /生成 SQL|校验 SQL|执行查询|组织报告/,
    )
    // 反例：旧 650ms 假定时器产物不含具体节点名 → 不应只剩「Agent 正在执行分析」之类占位
    expect(joined.length).toBeGreaterThan(0)
  })
})