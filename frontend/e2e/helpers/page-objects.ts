import type { Page } from '@playwright/test'
import { expect } from '@playwright/test'
import { FRONTEND_URL } from './env'

/**
 * Workbench 页面门面（选择器依据 P12 前 pending recon：Composer/RequirementCardView/
 * ReportPaper/ProgressCard/SessionRail/MessageBubbles 的稳定 class）。
 */
export class WorkbenchPage {
  readonly sendingText = '发送 ↗'

  constructor(private readonly page: Page) {}

  async open(): Promise<void> {
    await this.page.goto(`${FRONTEND_URL}/`)
  }

  // ── 会话 rail ──
  async startNewSession(): Promise<void> {
    await this.page.locator('.wb-new-btn').first().click()
    await this.page.waitForTimeout(300)
  }

  async selectSessionByTitle(title: string): Promise<void> {
    await this.page.locator('.wb-session', { hasText: title }).locator('.wb-session-main').click()
  }

  // ── composer ──
  async sendQuery(text: string): Promise<void> {
    await this.page.locator('input[aria-label="输入分析问题"]').fill(text)
    await this.page.getByRole('button', { name: this.sendingText }).click()
  }

  // ── 需求确认 ──
  async expectRequirementCard(): Promise<void> {
    await expect(this.page.locator('.wb-requirement-card')).toBeVisible()
  }

  async clickPrimary(actionLabel: string | null = null): Promise<void> {
    const btn = actionLabel
      ? this.page.getByRole('button', { name: actionLabel })
      : this.page.locator('.wb-primary')
    await expect(btn.first()).toBeVisible()
    await btn.first().click()
  }

  async confirmRequirement(): Promise<void> {
    await this.clickPrimary()
  }

  // ── 执行阶段可见性 ──
  async expectBusy(): Promise<void> {
    await expect(this.page.locator('.wb-progress-card').first()).toBeVisible({ timeout: 15_000 })
  }

  // ── 报告 ──
  async expectReport(): Promise<void> {
    await expect(this.page.locator('.wb-report-shell').first()).toBeVisible({ timeout: 60_000 })
  }

  async expectReportContains(text: string): Promise<void> {
    await expect(this.page.locator('.wb-report-shell').first()).toContainText(text, {
      timeout: 60_000,
    })
  }

  async expectEmptyBand(): Promise<void> {
    await expect(this.page.locator('.wb-empty-band').first()).toBeVisible({ timeout: 60_000 })
  }

  async expectErrorCard(): Promise<void> {
    await expect(this.page.locator('.wb-finding').first()).toBeVisible({ timeout: 60_000 })
  }

  // ── chitchat ──
  async expectAgentBubbleContains(text: string): Promise<void> {
    await expect(this.page.locator('.wb-bubble').first()).toContainText(text, { timeout: 15_000 })
  }

  // ── 停止按钮（background-execution spec） ──
  async stopGenerating(): Promise<void> {
    await this.page.getByRole('button', { name: '停止生成' }).click()
  }

  // ── toast ──
  async expectToastContains(text: string): Promise<void> {
    await expect(this.page.locator('body')).toContainText(text, { timeout: 30_000 })
  }
}