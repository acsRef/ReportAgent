import { Typography } from 'antd'
import type { ReportBlock } from '../../../types/report'

const { Text } = Typography

interface Props {
  block: ReportBlock
}

export default function TableBlock({ block }: Props) {
  const data = block.data as Record<string, unknown>
  const columns = (data.columns as Array<{ key: string; title: string }>) || []
  const rows = (data.rows as Record<string, unknown>[]) || []

  if (columns.length === 0) return null

  return (
    <div style={{
      background: '#FFFFFF',
      padding: '20px 0 0',
      borderRadius: 10,
      border: '1px solid #E2E8F0',
      boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
      overflow: 'hidden',
    }}>
      <div style={{
        fontSize: 15,
        fontWeight: 600,
        marginBottom: 12,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '0 20px',
        color: '#1E293B',
      }}>
        <div style={{ width: 3, height: 16, background: '#3B82F6', borderRadius: 2 }} />
        <span>{block.title || '数据明细'}</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: 13,
        }}>
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  style={{
                    background: '#F8FAFC',
                    padding: '10px 16px',
                    textAlign: 'left',
                    fontWeight: 600,
                    color: '#64748B',
                    borderBottom: '1px solid #E2E8F0',
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    position: 'sticky',
                    top: 0,
                    zIndex: 1,
                  }}
                >
                  {col.title || col.key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 50).map((row, idx) => (
              <tr
                key={idx}
                style={{
                  transition: 'background 0.15s',
                  background: idx % 2 === 0 ? '#FFFFFF' : '#FAFBFC',
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = '#EFF6FF' }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = idx % 2 === 0 ? '#FFFFFF' : '#FAFBFC'
                }}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    style={{
                      padding: '8px 16px',
                      borderBottom: '1px solid #F1F5F9',
                      color: '#1E293B',
                      fontSize: 13,
                    }}
                  >
                    {String(row[col.key] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
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