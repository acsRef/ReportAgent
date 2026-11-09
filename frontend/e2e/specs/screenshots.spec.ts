import { resolve } from 'node:path'
import { test, expect } from '@playwright/test'
import { repoRoot } from '../helpers/env'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

/**
 * 简历演示截图（真 backend :8100 + 真 LLM + 真 PG；不 mock）。
 * 前置：backend REPORTAGENT_E2E=1 + ragent-py :8000 + PG seed 30k 数据；vite 由 globalSetup 起。
 * 运行：cd frontend && npx playwright test e2e/specs/screenshots.spec.ts
 */
const SHOT_DIR = resolve(repoRoot, 'screenshots')

/** 报告全链（SQL+exec+report plan+chart）真 LLM 40-90s+——240s 稳。
 * ReportPaper mount 后异步 fetch 详情（loading 期间空 spinner shell 已可见）——
 * 必须等正文渲染完成（echarts **SVG** renderer→svg 元素；或 OVERVIEW 段落兜底），
 * 否则截图拍在加载中/空 shell。 */
async function expectReportLong(page: import('@playwright/test').Page): Promise<void> {
  await expect(page.locator('.wb-report-shell').first()).toBeVisible({ timeout: 240_000 })
  await page.waitForFunction(
    () => {
      const shell = document.querySelector('.wb-report-shell')
      if (!shell) return false
      return shell.querySelector('svg') !== null || (shell.textContent ?? '').includes('OVERVIEW')
    },
    undefined,
    { timeout: 240_000 },
  )
}

/** 直接截报告实体（shell 元素级——整页 fullPage 会因长页面切丢图表区）。
 * 截图前等 busy 遮罩（「Agent 正在处理中…」/progress card）消失——报告已 fetch 完
 * 但 SSE busy 清除可能滞后，遮罩残留会盖住图表。 */
async function shotReport(page: import('@playwright/test').Page, name: string): Promise<void> {
  const shell = page.locator('.wb-report-shell').first()
  await shell.scrollIntoViewIfNeeded()
  await page
    .waitForFunction(
      () => {
        const text = document.body.textContent ?? ''
        return !text.includes('正在处理中') && !document.querySelector('.wb-turn-progress')
      },
      undefined,
      { timeout: 60_000 },
    )
    .catch(() => {})
  await page.waitForTimeout(500)
  await shell.screenshot({ path: resolve(SHOT_DIR, name) })
  console.log()
}

async function shot(page: import('@playwright/test').Page, name: string): Promise<void> {
  await page.screenshot({ path: resolve(SHOT_DIR, name), fullPage: true })
  console.log(`[shot] ${name}`)
}

/** 真 LLM 需求常带缺项——「一次一问」逐题选首个选项，直到出现确认条。
 *  2026-11-09 起统一走 page-object 门面（旧的整卡 checkbox /「补充完成，查看确认」
 *  在 A4 之后已不存在，见 plan e2e-contract-realign）。 */
async function resolveRequirementCard(wb: WorkbenchPage): Promise<void> {
  await wb.resolveRequirementIfAsked()
}

test.describe('screenshots — 简历演示真实截图（真 LLM/真 backend）', () => {
  test.setTimeout(420_000)  // 真 LLM 全链（需求+SQL+报告×多轮）>120s，per-test 放宽

  test('S1 主链：需求卡 → 确认 → 完整报告', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()
    await shot(page, 'S1-requirement-card.png')
    await resolveRequirementCard(wb)

    await wb.confirmRequirement()
    await expectReportLong(page)
    await shotReport(page, 'S1-report.png')
  })

  test('S2 偏好记忆：以后都用柱状图 → 已记住 → 报告柱状图', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    // P16.5：explicit preference 图前短路（生产写入口）
    await wb.sendQuery('以后报告都用柱状图')
    await wb.expectAgentBubbleContains('已记住')
    await shot(page, 'S2-preference-remembered.png')

    await wb.sendQuery('2024年各区域销售额')
    await wb.expectRequirementCard()
    await resolveRequirementCard(wb)
    await wb.confirmRequirement()
    await expectReportLong(page)
    await shotReport(page, 'S2-report-bar.png')
  })

  test('S3 多轮会话：首查区域 → 续看月度趋势（上下文延续）', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额排名')
    await wb.expectRequirementCard()
    await resolveRequirementCard(wb)
    await wb.confirmRequirement()
    await expectReportLong(page)

    await wb.sendQuery('继续看看月度销售趋势')
    await wb.expectRequirementCard()
    await resolveRequirementCard(wb)
    await wb.confirmRequirement()
    await expectReportLong(page)
    await shotReport(page, 'S3-multiturn-monthly-report.png')
  })

  test('S4 需求澄清：缺少数据范围 → 补全后确认报告', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('看一下2024年销售情况')
    await wb.expectRequirementCard()

    const hasMissing = (await page.locator('.wb-ask-option').count()) > 0
    if (hasMissing) {
      await shot(page, 'S4-clarify-missing.png')
    } else {
      await shot(page, 'S4-clarify-complete.png')
    }
    await resolveRequirementCard(wb)

    await wb.confirmRequirement()
    await expectReportLong(page)
    await shotReport(page, 'S4-report.png')
  })

  test('S5 repair 演示：SQL 对象失败 → 诊断修复 → 成功报告', async ({ page }) => {
    // X-E2E-Fault（kind=object_not_found;mode=once）经 page.route 注入 confirm 请求——
    // P15 seam 确定性触发 repair（不靠 LLM 拼错彩票），后端走
    // validation-failed → DiagnosePolicy → MCP schema 刷新 → 重生成 → 成功。
    await page.route('**/api/v1/sessions/*/confirm', async (route) => {
      const headers = { ...route.request().headers(), 'X-E2E-Fault': 'kind=object_not_found;mode=once' }
      await route.continue({ headers })
    })
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年各区域销售额')
    await wb.expectRequirementCard()
    await resolveRequirementCard(wb)
    await wb.confirmRequirement()

    // 处理中状态（进度卡）——repair 过程的「进行时」证据
    await expect(page.locator('.wb-turn-progress').first()).toBeVisible({ timeout: 120_000 })
    await shot(page, 'S5-in-progress.png')

    await expectReportLong(page)
    await shotReport(page, 'S5-report-after-repair.png')
    await page.unroute('**/api/v1/sessions/*/confirm')
  })

  test('S6 跨会话 Query Memory：新会话延续上次分析结构', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()

    // 会话 A：查 2024 华东（成功 → Query Memory 写入）
    await wb.startNewSession()
    await wb.sendQuery('2024年华东区域销售额排名')
    await wb.expectRequirementCard()
    await resolveRequirementCard(wb)
    await wb.confirmRequirement()
    await expectReportLong(page)

    // 新会话 B（跨会话）：结构相近 query → Query Memory 参考帧
    await wb.startNewSession()
    await wb.sendQuery('同样看看华东2024年销售额')
    await wb.expectRequirementCard()
    await resolveRequirementCard(wb)
    await wb.confirmRequirement()
    await expectReportLong(page)
    await shotReport(page, 'S6-cross-session-query-memory.png')
  })
})
