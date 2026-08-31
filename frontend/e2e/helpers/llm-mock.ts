import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { BACKEND_URL, CONTRACT_FIXTURES_DIR, loadDotEnv, repoRoot } from './env'

const PYTHON = process.env.RAGENT_PYTHON ?? 'D:/miniConda/envs/agent/python.exe'
const BACKEND_DIR = resolve(repoRoot, 'backend')

/**
 * ============================================================
 * Contract E2E 边界定义（review-prep-r2 Fix 2 正式化）
 * ============================================================
 * Contract E2E = real browser + real FastAPI + real LangGraph
 *             + real PG + mock LLM + INTENTIONALLY DISABLED MCP
 *
 * 强制不连真实 MCP：`RAGENT_MCP_PYTHON=D:/non-existent/...` 让 RagMCPClient
 * 子进程启动失败 → dict_hit=False / schema 空 → 走 mock LLM + 本地 schema fallback。
 * 这证明：「MCP 不可用时，系统 fallback 后能工作」——不是 frontend → backend →
 * MCP → DB 全链路（那是 Full E2E 的范畴）。
 *
 * Full E2E 边界：real browser + real FastAPI + real LangGraph + real PG +
 * real LLM (MiniMax) + real MCP (ragent-py)。env `REPORTAGENT_E2E=1` gate。
 *
 * 适用场景：
 *   - Contract E2E：CI per-PR 跑（无外部 key 依赖，可靠）
 *   - Full E2E：nightly / manual（需真 LLM key + ragent-py 服务）
 * ============================================================
 */
const RAGENT_MCP_PYTHON = process.env.RAGENT_MCP_PYTHON ?? 'D:/non-existent/ragent-python.exe'

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