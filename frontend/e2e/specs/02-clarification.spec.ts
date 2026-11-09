import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('02-clarification — 缺字段 → 一次一问作答 → 确认 → 报告', () => {
  test.beforeAll(async () => {
    await startContractBackend('clarification')
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('缺指标时先问一次（不是整卡表单），答完出确认条并生成报告', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('帮我分析销售额')
    await wb.expectRequirementCard()

    // 一次一问（`slot_order` = metric > time_range > granularity > scope > analysis_intent）：
    // 指标由用户原话确定性填上（「销售额」在句子里），真正缺的是**分析视角** →
    // 渲染的是澄清提问，不是 2026-11-04 之前那张带 checkbox 组的整卡
    // （`RequirementCardView variant="full"` 已无生产 caller）。
    await expect(wb.currentAsk()).toHaveAttribute('aria-label', '需求澄清')
    await expect(wb.currentAsk()).toContainText('视角')
    await expect(wb.currentAsk().locator('.wb-ask-option').first()).toBeVisible()

    // 作答（点选项 = PATCH）→ 服务端推导下一个槽位；答完就是确认条
    await wb.resolveRequirementIfAsked()
    await expect(wb.currentAsk()).toHaveAttribute('aria-label', '需求确认')

    await wb.confirmRequirement()

    await wb.expectReport()
    await wb.expectReportContains('华南')
  })
})
