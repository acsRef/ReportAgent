import { useState } from 'react'
import { Typography } from 'antd'
import type { ReportBlock, TableMode } from '../../../types/report'
import { renderFlatTable, renderTreeTable, renderIndentTable, renderCrossTable } from '../tableModes'

const { Text } = Typography

interface Props {
  block: ReportBlock
}

const MODE_NAMES: Record<TableMode, string> = {
  flat: '平铺重复式',
  tree: '合并树形式',
  indent: '缩进层级式',
  cross: '交叉透视',
}

const MODE_LABELS: Record<TableMode, string> = {
  flat: '每行完整列出所有列，无合并、无缩进',
  tree: '父级信息通过 rowspan 纵向合并，只出现一次',
  indent: '无合并、无重复，靠缩进表示层级归属',
  cross: '行列交叉表，单元格为交叉值',
}

export default function TableBlock({ block }: Props) {
  const data = block.data as Record<string, unknown>
  const columns = (data.columns as Array<{ key: string; title: string }>) || []
  const rows = (data.rows as Record<string, unknown>[]) || []
  const [mode, setMode] = useState<TableMode>(block.mode || 'flat')

  if (columns.length === 0) return null

  const hoverStyle = (e: React.MouseEvent<HTMLElement>, on: boolean) => {
    ;(e.currentTarget as HTMLElement).style.background = on ? '#EFF6FF' : ''
  }
  const cellBg = (idx: number) => idx % 2 === 0 ? '#FFFFFF' : '#FAFBFC'

  const renderTable = () => {
    const p = { columns, rows, hoverStyle, cellBg }
    switch (mode) {
      case 'tree': return renderTreeTable(p)
      case 'indent': return renderIndentTable(p)
      case 'cross': return renderCrossTable(p)
      default: return renderFlatTable(p)
    }
  }

  return (
    <div style={{
      background: '#FFFFFF',
      borderRadius: 10,
      border: '1px solid #E2E8F0',
      boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '16px 20px 12px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 3, height: 16, background: '#3B82F6', borderRadius: 2 }} />
          <Text strong style={{ fontSize: 15, color: '#1E293B' }}>
            {block.title || '数据明细'}
          </Text>
          <Text style={{ fontSize: 12, color: '#94A3B8', fontWeight: 400 }}>
            {MODE_LABELS[mode]}
          </Text>
        </div>

        {/* Mode Tabs */}
        <div style={{ display: 'flex', gap: 2 }}>
          {(Object.keys(MODE_NAMES) as TableMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              style={{
                padding: '4px 12px',
                borderRadius: '4px 4px 0 0',
                fontSize: 11,
                cursor: 'pointer',
                border: 'none',
                background: mode === m ? '#FFFFFF' : 'transparent',
                color: mode === m ? '#1E40AF' : '#94A3B8',
                fontWeight: mode === m ? 500 : 400,
                borderBottom: mode === m ? '2px solid #1E40AF' : '2px solid transparent',
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                if (mode !== m) (e.currentTarget as HTMLElement).style.color = '#1E40AF'}}
              onMouseLeave={(e) => {
                if (mode !== m) (e.currentTarget as HTMLElement).style.color = '#94A3B8'}}
            >
              {MODE_NAMES[m]}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto', padding: '0 0 12px' }}>
        {renderTable()}
      </div>

      {rows.length > 50 && (
        <div style={{ padding: '10px 20px', textAlign: 'center', borderTop: '1px solid #F1F5F9' }}>
          <Text style={{ color: '#94A3B8', fontSize: 11 }}>
            仅显示前 50 条数据，共 {rows.length} 条
          </Text>
        </div>
      )}
    </div>
  )
}