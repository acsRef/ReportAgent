// Headless browser smoke test for ReportAgent workbench.
//
// Pre-requisites:
//   - Backend running on http://127.0.0.1:8100 (uvicorn)
//   - Frontend running on http://127.0.0.1:3000 (vite)
//   - Microsoft Edge installed at the path below
//
// Usage:
//   node browser_smoke.mjs
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

const EDGE_PATH = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://127.0.0.1:3000'
const SHOT_DIR = 'd:/tmp_browser_shots'

mkdirSync(SHOT_DIR, { recursive: true })

function log(msg) { console.log(`[smoke] ${msg}`) }

async function main() {
  const browser = await chromium.launch({
    executablePath: EDGE_PATH,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  const consoleErrors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push('pageerror: ' + err.message))

  async function shot(name) {
    const p = join(SHOT_DIR, `${name}.png`)
    await page.screenshot({ path: p, fullPage: false })
    log(`screenshot: ${p}`)
  }

  try {
    // 1. Open root → redirect to /login
    log('1. open /')
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForURL(/\/login$/, { timeout: 10000 })
    await shot('01-login')

    // 2. Login as admin
    log('2. login')
    await page.fill('input[id*="username" i], input[autocomplete="username"]', 'admin').catch(() => {})
    // antd 6 Input has no stable id; query by placeholder.
    await page.getByPlaceholder('admin').first().fill('admin')
    await page.getByPlaceholder('••••••••').first().fill('admin123')
    await page.getByRole('button', { name: '进入工作台' }).click()
    await page.waitForURL(`${BASE}/`, { timeout: 10000 })
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(800)
    await shot('02-workbench')

    // 2b. Click first historical session in the left rail
    log('2b. click historical session')
    // Click the first item that has '条 ·' text in any descendant
    const sessionRow = page.locator('.session-row').first()
    const before = await page.locator('text=需求卡').count()
    log(`   需求卡 before click: ${before}  candidate row: ${await sessionRow.count()}`)
    if (await sessionRow.count() > 0) {
      // Hook network to capture what /sessions/{sid} returns
      page.on('response', async (resp) => {
        if (resp.url().includes('/api/v1/sessions/') && !resp.url().endsWith('/sessions')) {
          try {
            const body = await resp.json()
            console.log('[net]', resp.url().split('?')[0].slice(-40), 'cur_req:', body.current_requirement ? 'YES(' + body.current_requirement.status + ')' : 'no', 'reports:', body.session?.report_versions?.length ?? 0)
          } catch {}
        }
      })
      await sessionRow.click(); console.log("[js] click dispatched to", await sessionRow.evaluate(el => el.outerHTML.slice(0, 120)))
      await page.waitForTimeout(4000)
      const after = await page.locator('text=需求卡').count()
      log(`   需求卡 after click: ${after}`)
      await shot('02b-historical-selected')
    }
    // 3. Verify left rail has session list (buckets)
    const todayHeader = await page.getByText('今天', { exact: false }).count()
    log(`   "今天" bucket header present: ${todayHeader > 0}`)

    // 4. Submit a new analysis
    log('4. submit analysis')
    await page.locator('textarea').first().fill('今年华东销售量趋势')
    await page.getByRole('button', { name: '提交分析' }).click()
    // Wait for the requirement card to appear
    await page.waitForSelector('text=需求卡', { timeout: 30000 })
    await page.waitForTimeout(1500)
    await shot('03-requirement-card')

    // 5. Confirm the requirement: click some options
    log('5. fill requirement fields')
    // Click each Radio.Button group option we can find; easiest is to
    // pick the first option per group then accept assumptions.
    // Accept all assumption rows: click any ✓ icon button in the requirement card
    const acceptBtns = page.locator('.anticon-check')
    const acceptCount = await acceptBtns.count()
    log(`   accept-assumption buttons found: ${acceptCount}`)
    for (let i = 0; i < acceptCount; i++) {
      await acceptBtns.nth(i).click({ force: true })
    }
    // Pick a "今年" radio
    const jinNian = page.getByText('今年', { exact: true })
    if (await jinNian.count() > 0) {
      await jinNian.first().click({ force: true })
    }
    await shot('04-requirement-filled')

    // 6. Click 确认执行
    log('6. confirm')
    const confirmBtn = page.getByRole('button', { name: '确认执行' })
    if (await confirmBtn.count() > 0) {
      await confirmBtn.first().click()
      // Wait for phase to become report_ready (phase tag) or for a bar chart
      try {
        await page.waitForSelector('text=报告版本', { timeout: 90000 })
      } catch {
        log('   (no 报告版本 section appeared within 90s — confirm may have errored)')
      }
    }
    await page.waitForTimeout(2000)
    await shot('05-after-confirm')

    // 7. Visit /templates
    log('7. templates page')
    await page.goto(`${BASE}/templates`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(500)
    await shot('06-templates')

    // 8. Click 新建模板
    log('8. create template')
    const newBtn = page.getByRole('button', { name: '新建模板' })
    if (await newBtn.count() > 0) {
      await newBtn.first().click()
      await page.waitForSelector('.ant-modal', { timeout: 5000 })
      await page.getByPlaceholder('例如：华东月销售分析').fill('E2E smoke 模板')
      await shot('07-new-template-modal')
      // antD Modal footer primary button — stable across label changes
      await page.locator('.ant-modal-footer .ant-btn-primary').first().click()
      await page.waitForTimeout(1500)
    }
    await shot('08-templates-after-create')

    // 9. Logout
    log('9. logout')
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(500)
    // Click user dropdown
    const userChip = page.locator('.ant-avatar').first()
    if (await userChip.count() > 0) {
      await userChip.click()
      await page.waitForTimeout(300)
      const logoutBtn = page.getByText('退出登录')
      if (await logoutBtn.count() > 0) {
        await logoutBtn.first().click()
        await page.waitForURL(/\/login$/, { timeout: 5000 })
      }
    }
    await shot('09-after-logout')

    log(`\n=== console errors collected: ${consoleErrors.length} ===`)
    for (const e of consoleErrors) log(`  ! ${e}`)
    log('\nDONE')
  } catch (err) {
    log(`FATAL: ${err.message}`)
    await shot('99-error')
    throw err
  } finally {
    await browser.close()
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
