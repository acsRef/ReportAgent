// Browser smoke for a specific user query.
//   "查询2024年各区域销售额排名"
// Reports phase, requirement card, report (chart), and any errors.
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const EDGE_PATH = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:3000'
const SHOT_DIR = 'd:/tmp_query_test'
const QUERY = '查询2024年各区域销售额排名'

mkdirSync(SHOT_DIR, { recursive: true })

const log = (m) => console.log(`[q] ${m}`)
const shot = async (page, name) => {
  const p = join(SHOT_DIR, `${name}.png`)
  await page.screenshot({ path: p, fullPage: false })
  log(`  shot: ${p}`)
  return p
}

async function main() {
  const browser = await chromium.launch({
    executablePath: EDGE_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const errs = []
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message))

  const net = []
  page.on('response', async (r) => {
    const u = r.url()
    if (u.includes('/api/v1/')) {
      try {
        const body = await r.json().catch(() => null)
        net.push({ status: r.status(), method: r.request().method(), url: u.slice(-60), body })
      } catch {}
    }
  })

  try {
    log('1. login')
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForURL(/\/login$/)
    await page.getByPlaceholder('admin').first().fill('admin')
    await page.getByPlaceholder('••••••••').first().fill('admin123')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await page.waitForURL(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(800)
    await shot(page, '01-workbench')

    log(`2. submit query: ${QUERY}`)
    await page.locator('textarea').first().fill(QUERY)
    const beforeNetCount = net.length
    await page.getByRole('button', { name: '提交分析' }).click()

    // Wait for requirement card body to render (LLM can take 5-30s, then React needs to hydrate)
    log('   waiting for requirement card...')
    await page.waitForSelector('button:has-text("确认执行")', { timeout: 120000 })
    const newNet = net.slice(beforeNetCount)
    log(`   api calls during chat: ${newNet.length}`)
    for (const n of newNet) {
      log(`   - ${n.status} ${n.method} ...${n.url}`)
    }
    await page.waitForTimeout(1000)
    await shot(page, '02-requirement-card')

    log('3. accept all assumptions + pick first option per radio group')
    // Click all ✓ check buttons
    const acceptBtns = page.locator('button:has(.anticon-check)')
    const acceptCount = await acceptBtns.count()
    log(`   accept buttons: ${acceptCount}`)
    for (let i = 0; i < acceptCount; i++) {
      await acceptBtns.nth(i).click({ force: true })
    }
    // For each Radio.Group, pick the first label inside.
    const radioGroups = page.locator('.ant-radio-group')
    const rgCount = await radioGroups.count()
    log(`   radio groups: ${rgCount}`)
    for (let i = 0; i < rgCount; i++) {
      const labels = radioGroups.nth(i).locator('label.ant-radio-button-wrapper').first()
      if (await labels.count() > 0) {
        await labels.click({ force: true })
      }
    }
    await page.waitForTimeout(500)
    await shot(page, '03-requirement-filled')

    log('4. click 确认执行')
    const beforeConfirm = net.length
    await page.getByRole('button', { name: '确认执行' }).first().click()

    // Wait for the report to materialize — success criterion is the page
    // reaching `phase=report_ready` (which ReportPaper's mounted effect
    // requires before it fetches /reports/{v}), or any svg/canvas, or a
    // "v1" label. The backend takes ~40-50s to plan+execute+assemble,
    // so be patient.
    log('   waiting for confirm to produce v1...')
    let reportSeen = false
    let chartSvgCount = 0
    for (let i = 0; i < 60; i++) {
      await page.waitForTimeout(3000)
      const versionCount = await page.locator('text=/v\\d+/').count()
      const sectionCount = await page.locator('text=报告版本').count()
      chartSvgCount = await page.locator('.report-paper svg, .report-paper canvas, .report-paper .ant-table, .report-paper .ant-table-wrapper').count()
      const phaseText = await page.locator('text=/phase:/i').first().textContent().catch(() => '')
      if (versionCount > 0 || sectionCount > 0 || chartSvgCount > 0 || (phaseText && phaseText.includes('report_ready'))) {
        reportSeen = true
        log(`   report detected after ${(i + 1) * 3}s (svg=${chartSvgCount}, phase=${phaseText?.trim()})`)
        break
      }
    }
    if (!reportSeen) log('   (no report within 180s — confirm may have errored)')
    else log(`   ✓ report rendered with ${chartSvgCount} chart/svg/table element(s)`)
    const newNet2 = net.slice(beforeConfirm)
    log(`   api calls during confirm: ${newNet2.length}`)
    for (const n of newNet2) {
      log(`   - ${n.status} ${n.method} ...${n.url}`)
    }
    await shot(page, '04-after-confirm')

    log('5. inspect chart + snapshot data via API')
    // We need the actual session_id; use any "active" session from the
    // recent /api/v1/chat 200 response.
    const lastChat = [...net].reverse().find((n) => n.method === 'POST' && n.url.endsWith('/chat'))
    log(`   last chat response: ${lastChat ? 'found' : 'missing'}`)

    await page.waitForTimeout(2000)
    await shot(page, '05-final')

    log('=== console errors ===')
    for (const e of errs) log(`  ! ${e}`)
    log('=== summary ===')
    log(`  query: ${QUERY}`)
    log(`  screenshots: ${SHOT_DIR}`)
    log(`  total api calls: ${net.length}`)
    log(`  errors: ${errs.length}`)
  } catch (err) {
    log(`FATAL: ${err.message}`)
    await shot(page, '99-error')
    throw err
  } finally {
    writeFileSync(join(SHOT_DIR, 'api-calls.json'), JSON.stringify(net, null, 2))
    await browser.close()
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
