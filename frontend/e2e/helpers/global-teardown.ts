import { execSync } from 'node:child_process'
import { existsSync, rmSync, readFileSync } from 'node:fs'
import { stopBackend } from './llm-mock'
import { VITE_PID_FILE } from './global-setup'

/** 杀进程树（Windows 用 taskkill /T 连子进程一起清）。 */
function killTree(pid: number): void {
  if (process.platform === 'win32') {
    try {
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' })
    } catch {
      /* 已退出 */
    }
  } else {
    try {
      process.kill(pid, 'SIGTERM')
    } catch {
      /* 已退出 */
    }
  }
}

export default async function globalTeardown(): Promise<void> {
  await stopBackend()
  if (existsSync(VITE_PID_FILE)) {
    const pid = Number(readFileSync(VITE_PID_FILE, 'utf-8'))
    if (Number.isFinite(pid)) killTree(pid)
    rmSync(VITE_PID_FILE, { force: true })
  }
  process.stderr.write('[globalTeardown] backend + vite 已关闭\n')
}