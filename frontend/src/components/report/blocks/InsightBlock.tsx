import { Typography } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import type { ReportBlock } from '../../../types/report'

const { Text } = Typography

interface Props {
  block: ReportBlock
}

export default function InsightBlock({ block }: Props) {
  const content = String((block.data as Record<string, unknown>).content || '')

  if (!content) return null

  const lines = content.split('\n').filter(Boolean)
  const isNumbered = lines.some((l) => /^\d+[.、]/.test(l))

  return (
    <div style={{
      background: 'linear-gradient(135deg, #EFF6FF, #F8FAFC)',
      border: '1px solid #DBEAFE',
      borderRadius: 10,
      padding: '20px 24px',
      boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        color: '#1E40AF',
        fontWeight: 600,
        marginBottom: 12,
        fontSize: 14,
      }}>
        <div style={{
          width: 24, height: 24, borderRadius: 12,
          background: '#1E40AF', display: 'flex', alignItems: 'center',
          justifyContent: 'center', color: '#FFFFFF', fontSize: 13,
        }}>
          <BulbOutlined />
        </div>
        AI 智能业务洞察
      </div>

      {isNumbered ? (
        <ol style={{
          paddingLeft: 20,
          margin: 0,
          color: '#1E293B',
          lineHeight: 1.8,
          fontSize: 13,
        }}>
          {lines.map((line, i) => {
            const cleaned = line.replace(/^\d+[.、]\s*/, '')
            return <li key={i} style={{ marginBottom: 6 }}>{cleaned}</li>
          })}
        </ol>
      ) : (
        <div style={{ paddingLeft: 0, color: '#1E293B', lineHeight: 1.8, fontSize: 13 }}>
          {lines.map((line, i) => (
            <Text key={i} style={{ display: 'block', marginBottom: 6, color: '#1E293B', fontSize: 13 }}>
              {line}
            </Text>
          ))}
        </div>
      )}
    </div>
  )
}