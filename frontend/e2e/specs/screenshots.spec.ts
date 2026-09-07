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
        return !text.includes('正在处理中') && !document.querySelector('.wb-progress-card')
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

/** 真 LLM 需求卡常带 missing_fields + assumptions——选选项 + 全部接受，置 ready。 */
async function fillAllMissingGroups(page: import('@playwright/test').Page): Promise<boolean> {
  const groups = page.locator('.wb-option-group')
  const n = await groups.count()
  let filled = false
  for (let i = 0; i < n; i++) {
    const group = groups.nth(i)
    const pill = group.locator('label.atelier-radio-pill').first()
    if (await pill.isVisible().catch(() => false)) {
      if ((await pill.locator('input:checked').count()) === 0) {
        await pill.click()
        filled = true
      }
      continue
    }
    const box = group.locator('label.atelier-checkbox').first()
    if (await box.isVisible().catch(() => false)) {
      if ((await box.locator('input:checked').count()) === 0) {
        await box.click()
        filled = true
      }
    }
  }
  // assumptions 全部「接受」（isDraftReadyForReview 要求 accepted !== null）
  const assumpts = page.locator('.wb-assumption')
  const na = await assumpts.count()
  for (let i = 0; i < na; i++) {
    const accept = assumpts.nth(i).getByRole('button', { name: '接受' }).first()
    if (await accept.isVisible().catch(() => false)) {
      await accept.click()
      filled = true
    }
  }
  return filled
}

/** 补全缺失组 → 「补充完成，查看确认」（enabled 后点）→ 等待信息完整。 */
async function resolveRequirementCard(page: import('@playwright/test').Page, wb: WorkbenchPage): Promise<void> {
  const filled = await fillAllMissingGroups(page)
  if (filled) {
    const btn = page.getByRole('button', { name: '补充完成，查看确认' })
    await expect(btn.first()).toBeEnabled({ timeout: 60_000 })
    await btn.first().click()
    await expect(page.locator('.wb-req-status')).toContainText('信息完整 · 待确认')
  }
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
    await resolveRequirementCard(page, wb)

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
    await resolveRequirementCard(page, wb)
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
    await resolveRequirementCard(page, wb)
    await wb.confirmRequirement()
    await expectReportLong(page)

    await wb.sendQuery('继续看看月度销售趋势')
    await wb.expectRequirementCard()
    await resolveRequirementCard(page, wb)
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

    const hasMissing = (await page.locator('.wb-option-group').count()) > 0
    if (hasMissing) {
      await shot(page, 'S4-clarify-missing.png')
    } else {
      await shot(page, 'S4-clarify-complete.png')
    }
    await resolveRequirementCard(page, wb)

    await wb.confirmRequirement()
    await expectReportLong(page)
    await shotReport(page, 'S4-report.png')
  })
})
