import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TableBlock from '../TableBlock'
import type { ReportBlock } from '../../../../types/report'

function makeBlock(rowCount: number): ReportBlock {
  const rows = Array.from({ length: rowCount }, (_, i) => ({
    月份: `M${i + 1}`,
    销售额: 100 + i,
    增长率: `${i}%`,
  }))
  return {
    id: 'table',
    type: 'table',
    title: '数据明细',
    data: {
      columns: [
        { key: '月份', title: '月份' },
        { key: '销售额', title: '销售额' },
        { key: '增长率', title: '增长率' },
      ],
      rows,
    },
  }
}

const visibleRows = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('tbody tr')).filter(
    (tr) => (tr as HTMLElement).style.display !== 'none',
  )

describe('TableBlock — flat evidence table', () => {
  it('shows only the first 3 rows with expand toggle and counter', () => {
    const { container } = render(<TableBlock block={makeBlock(5)} />)
    expect(visibleRows(container)).toHaveLength(3)
    expect(screen.getByText('显示 3 / 5 条')).toBeTruthy()
    expect(screen.getByText('展开更多明细 ↓')).toBeTruthy()
  })

  it('expands to all rows and collapses back', () => {
    const { container } = render(<TableBlock block={makeBlock(5)} />)
    fireEvent.click(screen.getByText('展开更多明细 ↓'))
    expect(visibleRows(container)).toHaveLength(5)
    expect(screen.getByText('显示 5 / 5 条')).toBeTruthy()
    fireEvent.click(screen.getByText('收起明细 ↑'))
    expect(visibleRows(container)).toHaveLength(3)
  })

  it('no toggle button when rows <= 3', () => {
    const { container } = render(<TableBlock block={makeBlock(2)} />)
    expect(visibleRows(container)).toHaveLength(2)
    expect(screen.queryByText('展开更多明细 ↓')).toBeNull()
    expect(screen.getByText('显示 2 / 2 条')).toBeTruthy()
  })

  it('legacy table modes and the bogus 层级 column are gone', () => {
    render(<TableBlock block={makeBlock(5)} />)
    expect(screen.queryByText('缩进层级式')).toBeNull()
    expect(screen.queryByText('交叉透视')).toBeNull()
    expect(screen.queryByText('合并树形式')).toBeNull()
    expect(screen.queryByText('平铺重复式')).toBeNull()
    expect(screen.queryByText('层级')).toBeNull()
  })

  it('numeric columns get .num right alignment, text columns do not', () => {
    const { container } = render(<TableBlock block={makeBlock(3)} />)
    const headers = Array.from(container.querySelectorAll('th'))
    const sales = headers.find((th) => th.textContent === '销售额')!
    const month = headers.find((th) => th.textContent === '月份')!
    const growth = headers.find((th) => th.textContent === '增长率')!
    expect(sales.className).toContain('num')
    expect(month.className).not.toContain('num')
    expect(growth.className).not.toContain('num') // "0%" is not numeric
  })

  // 2026-11-03：异常类按日聚合会返回 366 行，一次全铺开既卡又没法读。
  it('长结果展开只铺前 50 条，并在页脚说明总数', () => {
    const { container } = render(<TableBlock block={makeBlock(366)} />)
    fireEvent.click(screen.getByText('展开更多明细 ↓'))
    expect(visibleRows(container)).toHaveLength(50)
    expect(screen.getByText(/显示 50 \/ 366 条（长结果仅展开前 50 条/)).toBeTruthy()
  })

  it('短结果（≤50 行）仍然一次展开全部', () => {
    const { container } = render(<TableBlock block={makeBlock(12)} />)
    fireEvent.click(screen.getByText('展开更多明细 ↓'))
    expect(visibleRows(container)).toHaveLength(12)
    expect(screen.getByText('显示 12 / 12 条')).toBeTruthy()
  })
})
