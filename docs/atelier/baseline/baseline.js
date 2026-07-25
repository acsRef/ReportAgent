#!/usr/bin/env node
/**
 * Pixel baseline + regression runner.
 *
 * Captures full-page screenshots at multiple viewports into
 * `baseline/<viewport>/<name>.png`, then (if `--check` is passed) compares
 * them against the same files in `baseline/`.
 *
 * Requirements (must be installed before running):
 *   - node >= 18
 *   - playwright + chromium (`npx playwright install chromium`)
 *
 * Usage:
 *   node scripts/baseline.js capture       # write baseline screenshots
 *   node scripts/baseline.js check         # compare against baseline
 *   node scripts/baseline.js check --threshold 0.005  # custom diff ratio
 *
 * The script depends on the static server being run separately, e.g.
 *   node scripts/dev-server.cjs ../docs 8765
 *   BASE=http://127.0.0.1:8765/atelier node scripts/baseline.js capture
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import url from 'node:url'

const VIEWPORTS = [
  { name: 'desktop-1440', width: 1440, height: 900 },
  { name: 'tablet-1180', width: 1180, height: 800 },
  { name: 'mobile-880',  width: 880,  height: 720 },
]

const SCENES = [
  { name: '01-top',    selector: '[data-atom="button"]' },
  { name: '02-form',   selector: '[data-atom="text-field"]' },
  { name: '03-data',   selector: '[data-atom="kpi"]' },
  { name: '04-charts', selector: '[data-atom="chart-pie"]' },
  { name: '05-report', selector: '[data-atom="report-paper-full"]' },
  { name: '06-a11y',   selector: '[data-atom="command-bar"]' },
]

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8765'
const ROOT = path.dirname(path.dirname(url.fileURLToPath(import.meta.url)))
const HERE = path.join(ROOT, 'docs', 'atelier', 'baseline')

async function importPlaywright() {
  try {
    return await import('playwright')
  } catch {
    console.error('[baseline] playwright is not installed. Run: npm i -D playwright && npx playwright install chromium')
    process.exit(2)
  }
}

async function capture() {
  const { chromium } = await importPlaywright()
  const browser = await chromium.launch()
  try {
    for (const vp of VIEWPORTS) {
      const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 1 })
      const page = await ctx.newPage()
      await page.goto(BASE + '/index.html', { waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      for (const scene of SCENES) {
        const target = await page.$(scene.selector)
        if (!target) { console.warn(`[baseline] skip ${vp.name} / ${scene.name} (selector not found)`); continue }
        await target.scrollIntoViewIfNeeded()
        await page.waitForTimeout(120)
        const out = path.join(HERE, vp.name)
        await fs.mkdir(out, { recursive: true })
        await target.screenshot({ path: path.join(out, scene.name + '.png') })
        console.log(`[baseline] captured ${vp.name} / ${scene.name}`)
      }
      await ctx.close()
    }
  } finally {
    await browser.close()
  }
}

async function check(threshold) {
  const { default: pixelmatch } = await import('pixelmatch').catch(() => ({ default: null }))
  const { default: PNG } = await import('pngjs').then(m => m).catch(() => ({ default: null }))
  if (!pixelmatch || !PNG) {
    console.error('[baseline] pixelmatch + pngjs required for --check. Run: npm i -D pixelmatch pngjs')
    process.exit(2)
  }
  let failed = 0
  for (const vp of VIEWPORTS) {
    for (const scene of SCENES) {
      const baseline = path.join(HERE, vp.name, scene.name + '.png')
      const current = path.join(HERE.replace(/\/baseline$/, '/current'), vp.name, scene.name + '.png')
      try {
        const baseImg = PNG.sync.read(await fs.readFile(baseline))
        const curImg = PNG.sync.read(await fs.readFile(current))
        if (baseImg.width !== curImg.width || baseImg.height !== curImg.height) {
          console.error(`[baseline] dimension mismatch: ${vp.name}/${scene.name}`)
          failed++; continue
        }
        const diff = new PNG({ width: baseImg.width, height: baseImg.height })
        const num = pixelmatch(baseImg.data, curImg.data, diff.data, baseImg.width, baseImg.height, { threshold })
        if (num > 0) {
          const out = path.join(HERE, vp.name, scene.name + '.diff.png')
          await fs.writeFile(out, PNG.sync.write(diff))
          const ratio = num / (baseImg.width * baseImg.height)
          console.error(`[baseline] ${vp.name}/${scene.name} diff=${ratio.toFixed(4)} (saved ${out})`)
          failed++
        } else {
          console.log(`[baseline] ${vp.name}/${scene.name} OK`)
        }
      } catch (e) {
        console.error(`[baseline] ${vp.name}/${scene.name} error: ${e.message}`)
        failed++
      }
    }
  }
  if (failed > 0) { console.error(`[baseline] FAILED ${failed} checks`); process.exit(1); }
  console.log('[baseline] all checks passed')
}

const action = process.argv[2] || 'capture'
const threshold = Number((process.argv.find((a) => a.startsWith('--threshold=')) || '').slice(12)) || 0.001
if (action === 'capture') { await capture() }
else if (action === 'check') { await check(threshold) }
else { console.error(`[baseline] unknown action: ${action}`); process.exit(2) }
