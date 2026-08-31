import { defineConfig, devices } from '@playwright/test'

/**
 * P12 Playwright E2E（docs/plans/2026-08-30-p12-playwright.md D1/D2/D5）
 * - testDir = frontend/e2e/specs（工程位 frontend/e2e/，复用 frontend devDeps）
 * - browser = Playwright bundled chromium（弃 .e2elogs 的 Edge 硬编码）
 * - workers=1：Contract 每 spec 独占重启 :8100 后端（LLM_PROVIDER=mock），串行防端口冲突
 * - globalSetup 只验证 PG + 起一个 vite :3000；backend 由 spec beforeAll 启动
 */
export default defineConfig({
  testDir: './specs',
  // 失败用例的 trace + 截图 + DOM snapshot 落到项目内固定目录，方便 review
  // 直接 `git checkout e2e/artifacts/<project>/<spec>/test-failed-1.png` 查看
  // outputDir 相对 config 文件位置（frontend/e2e/），所以是 './artifacts'
  outputDir: './artifacts',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // list 给开发实时反馈；json 归档每次完整 report（含 timing + failure 路径）便于 CI 串接。
  // CI 跑 `e2e:contract:report` / `e2e:full:report` 时各自的产物分文件归档。
  reporter: [
    ['list'],
    ['json', { outputFile: './artifacts/playwright-report.json' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:3000',
    // 失败时保留 trace.zip（含视频 + 网络日志 + DOM snapshot）与 PNG 截图
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  globalSetup: './helpers/global-setup.ts',
  globalTeardown: './helpers/global-teardown.ts',
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})