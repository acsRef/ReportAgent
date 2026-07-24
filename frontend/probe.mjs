import { chromium } from 'playwright'
const browser = await chromium.launch({
  executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})
const page = await browser.newContext({ viewport: { width: 1440, height: 900 } }).then(c => c.newPage())
await page.goto('http://127.0.0.1:3000/', { waitUntil: 'networkidle' })
await page.waitForURL(/\/login$/)
await page.getByPlaceholder('admin').first().fill('admin')
await page.getByPlaceholder('••••••••').first().fill('admin123')
await page.getByRole('button', { name: '进入工作台' }).click()
await page.waitForURL('http://127.0.0.1:3000/')
await page.waitForTimeout(1500)
await page.locator('textarea').first().fill('查询2024年各区域销售额排名')
await page.getByRole('button', { name: '提交分析' }).click()
await page.waitForTimeout(30000)  // wait for LLM (longer)
// Dump the DOM around the work area
const html = await page.evaluate(() => {
  const main = document.querySelector('main')
  return main ? main.innerHTML.slice(0, 4000) : 'no main'
})
console.log('=== main innerHTML ===')
console.log(html)
  // After dump, look for confirm button
  const btns = await page.locator('button').allTextContents()
  log('buttons: ' + JSON.stringify(btns).slice(0, 400))
  const reqCard = await page.locator('text=需求卡').count()
  log('requirement card header: ' + reqCard)
  const phaseTag = await page.locator('text=/phase: /').count()
  log('phase tag: ' + phaseTag)
  if (reqCard > 0) log('CARD RENDERED OK')
  else log('CARD NOT RENDERED')

await browser.close()
