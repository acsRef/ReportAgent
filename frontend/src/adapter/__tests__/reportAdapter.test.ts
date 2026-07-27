import { describe, expect, it } from 'vitest'
import { adaptReport } from '../reportAdapter'

const FULL_ANSWER = {
  text: '查询完成',
  insight: '华东最高',
  table: {
    columns: [{ key: '区域', title: '区域' }],
    rows: [{ 区域: '华东', 销量: 100 }],
  },
  chart: {
    type: 'bar',
    config: { data: [{ 区域: '华东', 销量: 100 }] },
  },
}

describe('adaptReport', () => {
  it('maps insight / text / table / chart into ordered blocks', () => {
    const blocks = adaptReport({ answer: FULL_ANSWER } as any)
    expect(blocks.map((b) => b.type)).toEqual(['insight', 'markdown', 'table', 'chart'])
  })

  it('chart block carries rows at top-level data.data', () => {
    const blocks = adaptReport({ answer: FULL_ANSWER } as any)
    const chart = blocks.find((b) => b.type === 'chart')!
    expect((chart.data as any).data).toHaveLength(1)
    expect((chart.data as any).type).toBe('bar')
  })

  it('skips chart when config has no rows', () => {
    const answer = { ...FULL_ANSWER, chart: { type: 'bar', config: {} } }
    const blocks = adaptReport({ answer } as any)
    expect(blocks.some((b) => b.type === 'chart')).toBe(false)
  })

  it('skips table when rows are empty (degenerate backend shape)', () => {
    const answer = { ...FULL_ANSWER, table: { columns: [], rows: [] } }
    const blocks = adaptReport({ answer } as any)
    expect(blocks.some((b) => b.type === 'table')).toBe(false)
  })

  it('empty answer → no blocks', () => {
    expect(adaptReport({ answer: null } as any)).toEqual([])
  })
})
