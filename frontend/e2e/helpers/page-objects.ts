import type { Locator, Page } from '@playwright/test'
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
  /**
   * 2026-11-05（plan conversation-transcript-messages 修订 A）：需求区不再是整卡，
   * 「需求就绪」的稳定信号是那条助手消息上的 `aria-label`（澄清提问或确认条）。
   *
   * 2026-11-09（plan e2e-contract-realign D2）：**取 `.last()`**——历史轮次也在 DOM 里，
   * `.first()` 会对着上一轮立刻通过（假绿）；且参数卡改成按需展开后，
   * `.wb-req-status` / `.wb-req-summary` 默认**不存在**，要看就走 `openRequirementDetails()`。
   */
  currentAsk(): Locator {
    return this.page.locator('[aria-label="需求确认"], [aria-label="需求澄清"]').last()
  }

  async expectRequirementCard(): Promise<void> {
    await expect(this.currentAsk()).toBeVisible({ timeout: 90_000 })
  }

  /** 断言「这句话听懂了什么」——就是用户实际读到的那一句话。 */
  async expectRequirementAsked(text: string): Promise<void> {
    await expect(this.currentAsk()).toContainText(text, { timeout: 90_000 })
  }

  /** 澄清是「一次一问」：点一个选项就等于作答（PATCH → 服务端推导下一问）。 */
  async pickAskOption(label: string): Promise<void> {
    await this.currentAsk()
      .locator('.wb-ask-option', { hasText: label })
      .first()
      .click()
  }

  /** 点开「需求详情」，参数网格（`.wb-req-summary` / `.wb-req-status`）才出现在 DOM 里。 */
  async openRequirementDetails(): Promise<void> {
    await this.currentAsk().getByRole('button', { name: '需求详情' }).click()
    await expect(this.currentAsk().locator('.wb-req-summary')).toBeVisible({ timeout: 10_000 })
  }

  async closeRequirementDetails(): Promise<void> {
    const btn = this.currentAsk().getByRole('button', { name: '收起详情' })
    if (await btn.isVisible().catch(() => false)) await btn.click()
  }

  /**
   * 把「缺项澄清」答到可确认为止（Full spec / screenshots 用真 LLM，缺项条数不确定）。
   * 每轮点该问题的第一个选项；直到出现确认条（`.wb-ask-confirm`）。
   * 上限 6 轮：后端一次只问一个槽位，正常 ≤ 3。
   *
   * 2026-11-09（plan e2e-contract-realign）：点完必须**等这一问真的换掉**（新问题或确认条），
   * 不能 fixed sleep 后重查——PATCH 还没回来时同一题仍在，会连点两次（第二次点在
   * 即将卸载的元素上 → `option.click()` 直接失败，spec 07 在整轮跑时就是这么闪红的）。
   */
  async resolveRequirementIfAsked(): Promise<void> {
    for (let i = 0; i < 6; i++) {
      const confirmBtn = this.page.locator('[aria-label="需求确认"] .wb-ask-confirm').last()
      if (await confirmBtn.isVisible().catch(() => false)) return
      const ask = this.currentAsk()
      const option = ask.locator('.wb-ask-option').first()
      const before = await ask.innerText().catch(() => '')
      await option.waitFor({ state: 'visible', timeout: 90_000 })
      await option.click()
      await expect
        .poll(
          async () => {
            if (await confirmBtn.isVisible().catch(() => false)) return 'confirm'
            const now = await this.currentAsk().innerText().catch(() => '')
            return now !== before ? 'changed' : 'same'
          },
          { timeout: 30_000, message: '答完这一问后应出现下一个问题或确认条' },
        )
        .not.toBe('same')
    }
  }

  async clickPrimary(actionLabel: string | null = null): Promise<void> {
    const btn = actionLabel
      ? this.page.getByRole('button', { name: actionLabel })
      : this.page.locator('.wb-primary')
    await expect(btn.first()).toBeVisible()
    await btn.first().click()
  }

  /**
   * 确认并生成报告。精确到确认条里的那颗按钮——不再 `.wb-primary` 盲点第一个
   * （报告区、历史轮次里也可能有 primary，点错就是另一种 flake）。
   */
  async confirmRequirement(): Promise<void> {
    const btn = this.page.locator('[aria-label="需求确认"] .wb-ask-confirm').last()
    await expect(btn).toBeEnabled({ timeout: 30_000 })
    await btn.click()
  }

  // ── 执行阶段可见性 ──
  /** 进度现在长在助手消息里（`.wb-turn-progress`），不再是独立进度卡。 */
  async expectBusy(): Promise<void> {
    await expect(this.page.locator('.wb-turn-progress').first()).toBeVisible({ timeout: 15_000 })
  }

  /**
   * 等本轮真正结束（进度条卸载）。
   *
   * 2026-11-09（plan e2e-contract-realign D4）：报告 shell 一挂上就发下一条消息，
   * phase 可能还停在 `generating`，`chatModeForPhase` 就会把追问送进别的分支——
   * spec 06 的 `MockLLMMiss intent_classify:2` 就是这么来的。
   */
  async waitIdle(): Promise<void> {
    await expect(this.page.locator('.wb-turn-progress')).toHaveCount(0, { timeout: 120_000 })
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