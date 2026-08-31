import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/** repo root = e2e/helpers 上溯三级（helpers → e2e → frontend → repo） */
export const repoRoot = resolve(import.meta.dirname, '../../..')

export const BACKEND_URL = process.env.E2E_BACKEND ?? 'http://127.0.0.1:8100'
export const FRONTEND_URL = process.env.E2E_FRONTEND ?? 'http://127.0.0.1:3000'
export const VITE_PORT = 3000
export const PG_PORT = 5432

/** Contract 层 mock fixture 目录（backend/tests/fixtures/llm_responses/{case}.json） */
export const CONTRACT_FIXTURES_DIR = resolve(repoRoot, 'backend/tests/fixtures/llm_responses')

/** 读仓库根 .env 键值，spawn backend 子进程时合并进 env（.env 的 load_dotenv 不覆盖既有 env）。 */
export function loadDotEnv(file = resolve(repoRoot, '.env')): Record<string, string> {
  const out: Record<string, string> = {}
  if (!existsSync(file)) return out
  for (const line of readFileSync(file, 'utf-8').split(/\r?\n/)) {
    const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!m) continue
    let v = m[2].trim()
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1)
    }
    out[m[1]] = v
  }
  return out
}