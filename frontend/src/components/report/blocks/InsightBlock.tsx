import { Text } from '../../atelier/Typography'
import { IconBulb } from '../../ui/Icons'
import type { ReportBlock } from '../../../types/report'

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
      background: 'linear-gradient(135deg, var(--teal-soft), var(--canvas))',
      border: '1px solid var(--teal-pale)',
      borderRadius: 10,
      padding: '20px 24px',
      boxShadow: 'var(--shadow-card)',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        color: 'var(--teal-deep)',
        fontWeight: 600,
        marginBottom: 12,
        fontSize: 14,
      }}>
        <div style={{
          width: 24, height: 24, borderRadius: 12,
          background: 'var(--teal-deep)', display: 'flex', alignItems: 'center',
          justifyContent: 'center', color: 'var(--paper)', fontSize: 13,
        }}>
          <IconBulb />
        </div>
        AI 智能业务洞察
      </div>

      {isNumbered ? (
        <ol style={{
          paddingLeft: 20,
          margin: 0,
          color: 'var(--ink)',
          lineHeight: 1.8,
          fontSize: 13,
        }}>
          {lines.map((line, i) => {
            const cleaned = line.replace(/^\d+[.、]\s*/, '')
            return <li key={i} style={{ marginBottom: 6 }}>{cleaned}</li>
          })}
        </ol>
      ) : (
        <div style={{ paddingLeft: 0, color: 'var(--ink)', lineHeight: 1.8, fontSize: 13 }}>
          {lines.map((line, i) => (
            <Text key={i} style={{ display: 'block', marginBottom: 6, color: 'var(--ink)', fontSize: 13 }}>
              {line}
            </Text>
          ))}
        </div>
      )}
    </div>
  )
}
