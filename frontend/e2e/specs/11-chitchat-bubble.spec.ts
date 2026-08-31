import { test, expect } from '@playwright/test'
import { startFullBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'

const fullGate = !process.env.REPORTAGENT_E2E
test.skip(fullGate, 'Full E2E requires REPORTAGENT_E2E=1（CI 自动 skip；本地真实 LLM + 真实 MCP 跑）')

test.describe('11-chitchat-bubble — 闲聊意图走 keyword 路径，AgentBubble 显示 casual reply', () => {
  test.beforeAll(async () => {
    await startFullBackend()
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('「你好」命中关键词 → AgentBubble 显示 casual reply（无需求卡 / 无 error 态）', async ({ page }) => {
    await auth(page)
    await page.goto('http://127.0.0.1:3000/')
    await page.locator('.wb-new-btn').first().click()
    await page.waitForTimeout(300)
    await page.locator('input[aria-label="输入分析问题"]').fill('你好')
    await page.getByRole('button', { name: '发送 ↗' }).click()

    // chitchat：直接出 AgentBubble（requirements_analysis_graph._casual_reply 确定性文案）
    await expect(page.locator('.wb-bubble').first()).toContainText(
      '你好！我可以帮你分析数据库里的销售',
      { timeout: 30_000 },
    )
    // 闲聊不发需求卡 / 不发 error
    await expect(page.locator('.wb-requirement-card')).toHaveCount(0)
    await expect(page.locator('.wb-progress-card.failed')).toHaveCount(0)
  })
})