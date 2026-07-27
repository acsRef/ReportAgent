import { describe, expect, it } from 'vitest'
import { prepareChart } from '../blocks/chartPrepare'

const REGION_ROWS = [
  { 区域: '华东', 季度: 'Q1', 年份: 2024, 销量: 100 },
  { 区域: '华东', 季度: 'Q2', 年份: 2024, 销量: 150 },
  { 区域: '华北', 季度: 'Q1', 年份: 2024, 销量: 80 },
  { 区域: '华北', 季度: 'Q2', 年份: 2024, 销量: 90 },
]

describe('prepareChart — field inference', () => {
  it('infers x/y/series from backend chart shape (no xField/yField)', () => {
    // Backend chart_advisor emits {type, config:{data: rows}} with no
    // field hints — the block must infer them or the chart renders NaN.
    const p = prepareChart({ type: 'bar', config: { data: REGION_ROWS } })
    expect(p.chartType).toBe('bar')
    expect(p.rows).toHaveLength(4)
    expect(p.yField).toBe('销量')       // last numeric column
    expect(p.xField).toBe('季度')       // last categorical column
    expect(p.seriesField).toBe('区域')  // remaining categorical → grouped series
  })

  it('reads top-level data rows (adapter output shape)', () => {
    const p = prepareChart({ type: 'line', data: REGION_ROWS, config: {} })
    expect(p.rows).toHaveLength(4)
    expect(p.yField).toBe('销量')
  })

  it('respects explicit xField/yField and skips series inference', () => {
    const p = prepareChart({
      type: 'bar',
      data: REGION_ROWS,
      config: { xField: '区域', yField: '销量' },
    })
    expect(p.xField).toBe('区域')
    expect(p.yField).toBe('销量')
    expect(p.seriesField).toBeNull()
  })

  it('single categorical → no series grouping', () => {
    const rows = [
      { 区域: '华东', 销售额: 100 },
      { 区域: '华北', 销售额: 80 },
    ]
    const p = prepareChart({ type: 'bar', data: rows, config: {} })
    expect(p.xField).toBe('区域')
    expect(p.yField).toBe('销售额')
    expect(p.seriesField).toBeNull()
  })

  it('pie infers name from categorical and value from numeric', () => {
    const p = prepareChart({ type: 'pie', data: REGION_ROWS, config: {} })
    expect(p.xField).toBe('季度')
    expect(p.yField).toBe('销量')
    expect(p.seriesField).toBeNull() // pie never groups
  })

  it('empty rows → safe empty defaults, no crash', () => {
    const p = prepareChart({ type: 'bar', config: { data: [] } })
    expect(p.rows).toEqual([])
    expect(p.xField).toBe('')
    expect(p.yField).toBe('')
    expect(p.seriesField).toBeNull()
  })

  it('defaults to bar for unknown/missing type', () => {
    const p = prepareChart({ data: REGION_ROWS })
    expect(p.chartType).toBe('bar')
  })
})
