import { useState } from 'react'
import { Text } from '../../atelier/Typography'
import type { ReportBlock } from '../../../types/report'

interface Props {
  block: ReportBlock
}

const VISIBLE_ROWS = 3

/**
 * Flat evidence table per docs/intelligent-analysis-workbench.html:
 * ONE plain table (no modes, no hierarchy column), numeric columns
 * right-aligned with tabular figures, first 3 rows visible and the
 * rest behind a single 展开更多明细 / 收起明细 toggle.
 */
export default function TableBlock({ block }: Props) {
  const data = block.data as Record<string, unknown>
  const columns = (data.columns as Array<{ key: string; title: string }>) || []
  const rows = (data.rows as Record<string, unknown>[]) || []
  const [expanded, setExpanded] = useState(false)

  if (columns.length === 0) return null

  // `data.empty` is set by reportAdapter when the SQL ran cleanly but
  // returned no rows (execution_status=EMPTY from the backend). We
  // show an explicit "未找到匹配记录" hint instead of a header-only
  // table that looks like a rendering bug.
  const isEmpty = data.empty === true

  const numericKeys = new Set(
    columns
      .filter(({ key }) =>
        rows.length > 0 &&
        rows.every((row) => {
          const value = row[key]
          return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
        }),
      )
      .map(({ key }) => key),
  )
  const shown = expanded ? rows.length : Math.min(VISIBLE_ROWS, rows.length)

  return (
    <div
      style={{
        background: 'var(--paper)',
        borderRadius: 10,
        border: '1px solid var(--line)',
        boxShadow: 'var(--shadow-card)',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: '16px 20px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 3, height: 16, background: 'var(--teal)', borderRadius: 2 }} />
        <Text strong style={{ fontSize: 15, color: 'var(--ink)' }}>
          {block.title || '数据明细'}
        </Text>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr>
              {columns.map((column) => {
                const isNum = numericKeys.has(column.key)
                return (
                  <th
                    key={column.key}
                    className={isNum ? 'num' : undefined}
                    style={{
                      padding: '8px 9px',
                      background: 'var(--canvas)',
                      color: 'var(--muted)',
                      fontSize: 12,
                      fontWeight: 600,
                      textAlign: isNum ? 'right' : 'left',
                      borderBottom: '1px solid var(--line-2)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {column.title}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  style={{
                    padding: '32px 9px',
                    textAlign: 'center',
                    color: 'var(--muted)',
                    fontSize: 13,
                  }}
                >
                  {isEmpty ? '未找到匹配记录' : '暂无数据'}
                </td>
              </tr>
            ) : (
              rows.map((row, rowIndex) => (
                <tr key={rowIndex} style={rowIndex >= shown ? { display: 'none' } : undefined}>
                  {columns.map((column) => {
                    const isNum = numericKeys.has(column.key)
                    return (
                      <td
                        key={column.key}
                        className={isNum ? 'num' : undefined}
                        style={{
                          padding: '9px',
                          borderBottom: '1px solid var(--line)',
                          color: 'var(--ink-2)',
                          textAlign: isNum ? 'right' : 'left',
                          fontVariantNumeric: isNum ? 'tabular-nums' : undefined,
                        }}
                      >
                        {String(row[column.key] ?? '')}
                      </td>
                    )
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div
        style={{
          background: 'var(--canvas)',
          borderTop: '1px solid var(--line)',
          padding: '8px 20px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Text style={{ fontSize: 12, color: 'var(--muted)' }}>
          {rows.length === 0 ? '0 条记录' : `显示 ${shown} / ${rows.length} 条`}
        </Text>
        {rows.length > VISIBLE_ROWS && (
          <button
            onClick={() => setExpanded((current) => !current)}
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--teal-deep)',
              fontSize: 12,
              cursor: 'pointer',
              padding: 0,
            }}
          >
            {expanded ? '收起明细 ↑' : '展开更多明细 ↓'}
          </button>
        )}
      </div>
    </div>
  )
}
