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

// 2026-11-04：后端说 table 就是画不了图（单列数值 / 无可比维度）——不许强画柱状图。
describe('prepareChart — 不可视化时退回表格', () => {
  it('type=table 直接返回 table（不再被强转成 bar）', () => {
    const p = prepareChart({ type: 'table', config: { data: [{ 销量: 1 }] } })

    expect(p.chartType).toBe('table')
    expect(p.rows).toEqual([])
  })

  it('只有一列数值（x 只能退到 y 自己）→ table', () => {
    const p = prepareChart({ type: 'bar', config: { data: [{ 销量: 1 }, { 销量: 8 }] } })

    expect(p.chartType).toBe('table')
  })

  it('有真实维度时仍然是可画的柱状图（回归）', () => {
    const p = prepareChart({ type: 'bar', config: { data: REGION_ROWS } })

    expect(p.chartType).toBe('bar')
    expect(p.xField).not.toBe(p.yField)
  })
})
