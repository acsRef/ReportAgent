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
})
