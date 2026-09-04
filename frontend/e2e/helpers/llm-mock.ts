import { spawn, execSync, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { BACKEND_URL, CONTRACT_FIXTURES_DIR, loadDotEnv, repoRoot } from './env'

/**
 * CI 修复：Python 解释器必须能跨平台解析——
 *  Windows 本地：开发机 conda（默认 D:/miniConda/envs/agent/python.exe）；可由
 *    `RAGENT_PYTHON` env 覆盖（playwright.config / 直接调试）。
 *  Linux/Ubuntu（CI runner）：Conda 不存在；actions/setup-python@v5 提供 `python3`；
 *    也可由 `RAGENT_PYTHON` env 显式覆盖（先验，不污染 env 来源单一）。
 *  没有 RAGENT_PYTHON 时按平台探测（Windows 保持原默认，Linux 优先 python3 / python）。
 * 仍找不到 → 抛 FileNotFoundError 让 spec 早失败而非 silent ENOENT 跑完全部。
 */
function resolvePython(): string {
  const fromEnv = process.env.RAGENT_PYTHON
  if (fromEnv) {
    if (!existsSync(fromEnv)) {
      throw new FileNotFoundError(
        `RAGENT_PYTHON=${fromEnv} 路径不存在——CI runner 装的是 actions/setup-python；本地若有自定义 conda 设一下。`
      )
    }
    return fromEnv
  }
  if (process.platform === 'win32') {
    return 'D:/miniConda/envs/agent/python.exe'
  }
  // Linux/macOS：先找 python3（actions/setup-python 默认 PATH），再退到 python
  for (const cand of ['python3', 'python']) {
    try {
      execSync(`${cand} -c "import sys; sys.exit(0)"`, { stdio: 'ignore' })
      return cand
    } catch (_) { /* try next */ }
  }
  throw new FileNotFoundError(
    '无可用 Python 解释器：CI runner 期待 actions/setup-python 提供 python3；本地 macOS/Linux 需 python3 或 python 在 PATH。'
  )
}

const PYTHON = resolvePython()
const BACKEND_DIR = resolve(repoRoot, 'backend')
// Contract 强制不连真实 MCP：指向不存在的解释器 → RagMCPClient 子进程起不来，
// dict_hit=False → intent 走 mock LLM；schema 空 → plan/generate 由 fixture 直接
// 返回可执行 SQL（对真实 seeded PG 跑）。fixture key 是语义 kind，不依赖 schema 漂移。
const RAGENT_MCP_PYTHON = process.env.RAGENT_MCP_PYTHON ?? '/non-existent/ragent-python'

let backend: ChildProcess | null = null

function backendAlive(): boolean {
  return backend !== null && backend.exitCode === null && backend.signalCode === null
}

async function waitForHealth(url: string, timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let last = ''
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url)
      if (res.ok) return
      last = `status ${res.status}`
    } catch (err) {
      last = err instanceof Error ? err.message : String(err)
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`backend health 超时（${timeoutMs}ms）${url} last=${last}`)
}

/**
 * 启动/重启一个 Contract 后端（uvicorn :8100，LLM_PROVIDER=mock）。
 * 每个 spec 独占一次（workers=1 串行），保证 mock 的 kind:seq 计数器从 0 开始，
 * 同一 case 的 fixture key 序列可预测（happy: sql_plan:1 → sql_generate:1 → report_plan:1）。
 *
 * `delayMs` 仅 mock 模式生效：让每次 mock LLM 调用先 sleep N ms，扩展 generating 窗口，
 * 让停止按钮（spec 07 background-execution）可确定性点击。LLM_MOCK_DELAY_MS env。
 */
export async function startContractBackend(caseId: string, delayMs = 0): Promise<void> {
  await stopBackend()
  const env = {
    ...process.env,
    ...loadDotEnv(),
    LLM_PROVIDER: 'mock',
    LLM_MOCK_CASE: caseId,
    LLM_MOCK_DIR: CONTRACT_FIXTURES_DIR,
    RAGENT_MCP_PYTHON,
    LLM_MOCK_DELAY_MS: String(delayMs),
  }
  backend = spawn(PYTHON, ['-m', 'uvicorn', 'app.main:app', '--port', '8100'], {
    cwd: BACKEND_DIR,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  backend.stdout?.on('data', (d) => process.stderr.write(`[backend:${caseId}] ${d.toString()}`))
  backend.stderr?.on('data', (d) => process.stderr.write(`[backend:${caseId}] ${d.toString()}`))
  backend.on('exit', (code, sig) => {
    process.stderr.write(`[backend:${caseId}] exited code=${code} sig=${sig}\n`)
  })
  await waitForHealth(`${BACKEND_URL}/health`)
}

export async function stopBackend(): Promise<void> {
  if (!backendAlive()) {
    backend = null
    return
  }
  const proc = backend
  backend = null
  proc.kill()
  await new Promise<void>((done) => {
    proc.once('exit', () => done())
    setTimeout(done, 3_000)
  })
}

/**
 * Full 层后端：真实 LLM（MiniMax）+ 真实 MCP（ragent-py）。
 * 走 .env 配置；由 spec 顶部 `test.skip(!process.env.REPORTAGENT_E2E)` 守门（CI 自动 skip）。
 */
export async function startFullBackend(): Promise<void> {
  await stopBackend()
  const env = {
    ...process.env,
    ...loadDotEnv(),
    APP_ENV: process.env.APP_ENV ?? 'development',
    ALLOW_INSECURE_DEFAULT_AUTH: process.env.ALLOW_INSECURE_DEFAULT_AUTH ?? '1',
  }
  backend = spawn(PYTHON, ['-m', 'uvicorn', 'app.main:app', '--port', '8100'], {
    cwd: BACKEND_DIR,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  backend.stdout?.on('data', (d) => process.stderr.write(`[backend:full] ${d.toString()}`))
  backend.stderr?.on('data', (d) => process.stderr.write(`[backend:full] ${d.toString()}`))
  backend.on('exit', (code, sig) => {
    process.stderr.write(`[backend:full] exited code=${code} sig=${sig}\n`)
  })
  await waitForHealth(`${BACKEND_URL}/health`)
}