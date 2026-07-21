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
  const isNumbered = lines.some((l) => /^\d+[\.\、]/.test(l))

  return (
    <div style={{
      background: '#f8faff',
      border: '1px solid #adc6ff',
      borderRadius: 8,
      padding: '18px 20px',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        color: '#1d39c4',
        fontWeight: 600,
        marginBottom: 10,
        fontSize: 14,
      }}>
        <BulbOutlined style={{ fontSize: 16 }} />
        AI 智能业务洞察
      </div>

      {isNumbered ? (
        <ol style={{
          paddingLeft: 20,
          margin: 0,
          color: '#262626',
          lineHeight: 1.8,
          fontSize: 13,
        }}>
          {lines.map((line, i) => {
            const cleaned = line.replace(/^\d+[\.\、]\s*/, '')
            return <li key={i} style={{ marginBottom: 4 }}>{cleaned}</li>
          })}
        </ol>
      ) : (
        <div style={{
          paddingLeft: 0,
          color: '#262626',
          lineHeight: 1.8,
          fontSize: 13,
        }}>
          {lines.map((line, i) => (
            <Text key={i} style={{ display: 'block', marginBottom: 4, color: '#262626', fontSize: 13 }}>
              {line}
            </Text>
          ))}
        </div>
      )}
    </div>
  )
}
