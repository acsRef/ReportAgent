import { Typography } from 'antd'
import { TableOutlined } from '@ant-design/icons'
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
      background: '#fff',
      padding: '20px 24px',
      borderRadius: 8,
      border: '1px solid #e8e8e8',
      boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
    }}>
      <div style={{
        fontSize: 15,
        fontWeight: 600,
        marginBottom: 16,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        <TableOutlined style={{ color: '#1677ff' }} />
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
                    background: '#fafafa',
                    padding: '10px 12px',
                    textAlign: 'left',
                    fontWeight: 600,
                    color: '#646a73',
                    borderBottom: '1px solid #e8e8e8',
                    fontSize: 12,
                    whiteSpace: 'nowrap',
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
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = '#fafafa' }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    style={{
                      padding: '10px 12px',
                      borderBottom: '1px solid #f0f0f0',
                      color: '#1f2329',
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
        <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginTop: 12, fontSize: 11 }}>
          仅显示前 50 条数据，共 {rows.length} 条
        </Text>
      )}
    </div>
  )
}
