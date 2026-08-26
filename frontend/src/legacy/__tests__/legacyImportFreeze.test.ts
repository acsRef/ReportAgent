/**
 * P1 Legacy Import Freeze — frontend 侧（docs/plans/2026-08-26-p1-architecture-freeze.md）。
 * src/ 下除 legacy/ 自身外，禁止任何文件 import 进入 legacy/。
 * 唯一豁免：App.tsx 的旧路由入口（/legacy/chat 等，Phase 15 随路由一并处置）。
 * 纯 Node fs 扫描，无渲染、无网络。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', '..')
const BRIDGE_FILES = new Set(['App.tsx'])

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) {
      if (relative(SRC, p).startsWith('legacy')) return [] // legacy 自身豁免
      return walk(p)
    }
    return /\.(ts|tsx)$/.test(name) ? [p] : []
  })
}

describe('legacy import freeze', () => {
  it('no file outside src/legacy imports from legacy/', () => {
    const violations: string[] = []
    for (const f of walk(SRC)) {
      if (BRIDGE_FILES.has(relative(SRC, f).replaceAll('\\', '/'))) continue // 桥接豁免
      const text = readFileSync(f, 'utf-8')
      if (/(?:from\s+|import\(\s*)['"][^'"]*\/legacy\//.test(text)) {
        violations.push(relative(SRC, f))
      }
    }
    expect(violations, `新代码禁止 import src/legacy（P1 冻结）:\n${violations.join('\n')}`).toEqual([])
  })

  it('bridge files are frozen to exactly the known legacy route entries', () => {
    for (const name of BRIDGE_FILES) {
      const text = readFileSync(join(SRC, name), 'utf-8')
      const mods = [...text.matchAll(/from '([^']*\/legacy\/[^']*)'/g)].map((m) => m[1])
      expect(mods.sort(), `${name} 桥接 import 快照漂移——确需新增先改本快照并过评审`).toEqual([
        './legacy/pages/ChatPage',
        './legacy/pages/HistoryPage',
        './legacy/pages/TemplateCenter',
      ])
    }
  })

  it('legacy/ directory exists (归置已完成)', () => {
    expect(statSync(join(SRC, 'legacy')).isDirectory()).toBe(true)
  })
})

