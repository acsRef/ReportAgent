import { test, expect } from '@playwright/test'
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

    // Fix 3：repair 真收敛到 SUCCESS（不是落到 FAILED 走伪成功）
    // 用 page.evaluate 走浏览器 context（拿 localStorage ragent_auth 的 token），
    // 避免 page.request 不继承 localStorage 的问题。
    const sid = await page.evaluate(async () => {
      const raw = localStorage.getItem('ragent_auth')
      const token = raw ? (JSON.parse(raw) as { state: { token: string } }).state.token : ''
      const sessionsResp = await fetch('/api/v1/sessions?limit=5', {
        headers: { Authorization: `Bearer ${token}` },
      })
      const sessionsJson = (await sessionsResp.json()) as { sessions?: { session_id: string }[] }
      return sessionsJson.sessions?.[0]?.session_id ?? null
    })
    expect(sid, 'latest session id 存在').toBeTruthy()

    const execStatus = await page.evaluate(async (sessionId) => {
      const raw = localStorage.getItem('ragent_auth')
      const token = raw ? (JSON.parse(raw) as { state: { token: string } }).state.token : ''
      const r = await fetch(`/api/v1/sessions/${sessionId}/reports/1`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const j = (await r.json()) as { report?: { execution_status: string } }
      return j.report?.execution_status
    }, sid)
    expect(
      execStatus,
      'repair 路径必须收敛到 SUCCESS（而非 FAILED 走伪成功）',
    ).toBe('SUCCESS')
  })
})