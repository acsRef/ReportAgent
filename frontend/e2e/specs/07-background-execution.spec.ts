import { test, expect } from '@playwright/test'
import { startContractBackend, stopBackend } from '../helpers/llm-mock'
import { auth } from '../helpers/auth'
import { WorkbenchPage } from '../helpers/page-objects'

test.describe('07-background-execution — 停止 → 后台跑完 → 5s 轮询通知', () => {
  test.beforeAll(async () => {
    // 拉长 generating 窗口让停止按钮可确定性点击（避免 validate_sql 拒绝 pg_sleep）
    await startContractBackend('background-execution', 3000)
  }, { timeout: 120_000 })
  test.afterAll(async () => {
    await stopBackend()
  })

  test('生成中点击停止，任务继续后台跑完，轮询出 toast', async ({ page }) => {
    await auth(page)
    const wb = new WorkbenchPage(page)
    await wb.open()
    await wb.startNewSession()

    await wb.sendQuery('2024年华南区销售额')
    await wb.expectRequirementCard()
    await wb.confirmRequirement()

    // 停止生成（fixture 用 pg_sleep(3) 拉长 generating 窗口，确保按钮可点）
    const stopBtn = page.getByRole('button', { name: '停止生成' })
    await expect(stopBtn).toBeVisible({ timeout: 20_000 })
    await stopBtn.click()

    // 前端停止显示 + 5s 轮询 → 后台完成 → toast 通知
    await expect(page.locator('body')).toContainText('报告已在后台生成，可查看', {
      timeout: 40_000,
    })

    // Fix 2：锁住后台跑完的因果链——前端只显示 toast 不够，必须证明
    //   stop → backend 任务继续 → 落库 → 前端轮询看到 report_ready → toast
    const { sid, phase, execStatus } = await page.evaluate(async () => {
      const raw = localStorage.getItem('ragent_auth')
      const token = raw
        ? (JSON.parse(raw) as { state: { token: string } }).state.token
        : ''
      const sessionsResp = await fetch('/api/v1/sessions?limit=5', {
        headers: { Authorization: `Bearer ${token}` },
      })
      const sj = (await sessionsResp.json()) as {
        sessions?: { session_id: string; phase: string }[]
      }
      const latest = sj.sessions?.[0]
      if (!latest) return { sid: null, phase: null, execStatus: null }
      const r = await fetch(
        `/api/v1/sessions/${latest.session_id}/reports/1`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      const rj = (await r.json()) as { report?: { execution_status: string } }
      return {
        sid: latest.session_id,
        phase: latest.phase,
        execStatus: rj.report?.execution_status ?? null,
      }
    })
    expect(sid, '最新 session id 存在').toBeTruthy()
    expect(phase, 'session.phase 翻到 report_ready').toBe('report_ready')
    expect(execStatus, 'report v1 真落库（execution_status=SUCCESS）').toBe(
      'SUCCESS',
    )
  })
})