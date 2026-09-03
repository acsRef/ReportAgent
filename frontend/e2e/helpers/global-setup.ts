import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import { resolve } from 'node:path'
import { FRONTEND_URL, PG_PORT, VITE_PORT, repoRoot } from './env'

export const VITE_PID_FILE = resolve(import.meta.dirname, '.e2e-vite.pid')

function tcpReachable(port: number, host = '127.0.0.1', timeoutMs = 4_000): Promise<boolean> {
  return new Promise((res) => {
    const sock = net.connect({ host, port })
    const done = (ok: boolean): void => {
      sock.destroy()
      res(ok)
    }
    sock.once('connect', () => done(true))
    sock.once('error', () => done(false))
    sock.setTimeout(timeoutMs, () => done(false))
  })
}

async function waitForHttp(url: string, timeoutMs = 120_000): Promise<void> {
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
  throw new Error(`前端 dev server 启动超时 ${url} last=${last}`)
}

/**
 * P12 globalSetup：只验证 PG 在跑、启动一个 vite dev server（:3000）供全部 spec 复用。
 * backend 由各 spec 的 beforeAll 用 startContractBackend(caseId) 独占启动（mock 计数归零）。
 */
export default async function globalSetup(): Promise<void> {
  if (!(await tcpReachable(PG_PORT))) {
    throw new Error(
      `PostgreSQL 未就绪（127.0.0.1:${PG_PORT}）。请先启动 ragent-postgres 容器并灌 init_pg + seed_pg。`,
    )
  }
  if (!(await tcpReachable(VITE_PORT))) {
    // CI 修复：显式 --host 127.0.0.1——Ubuntu runner 上 vite 默认可能绑 ::1，
    // 与 probe 的 127.0.0.1 不一致导致「dev server 启动超时」。
    const vite = spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1'], {
      cwd: resolve(repoRoot, 'frontend'),
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: process.platform === 'win32',
    })
    vite.stdout?.on('data', (d) => process.stderr.write(`[vite] ${d.toString()}`))
    vite.stderr?.on('data', (d) => process.stderr.write(`[vite] ${d.toString()}`))
    vite.on('exit', (code) => process.stderr.write(`[vite] exited ${code}\n`))
    await waitForHttp(FRONTEND_URL)
    if (vite.pid) writeFileSync(VITE_PID_FILE, String(vite.pid))
  }
  process.stderr.write('[globalSetup] PG 就绪 + vite 就绪；backend 由各 spec 独占启动\n')
}

export function vitePidFileWritten(): boolean {
  return existsSync(VITE_PID_FILE)
}