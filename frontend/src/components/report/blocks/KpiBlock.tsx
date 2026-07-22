import { Typography, Card } from 'antd'
import { ArrowUpOutlined, ArrowDownOutlined, MinusOutlined } from '@ant-design/icons'
import type { ReportBlock } from '../../../types/report'

const { Text } = Typography

interface Props {
  block: ReportBlock
}

export default function KpiBlock({ block }: Props) {
  const data = block.data as Record<string, unknown>
  const items = (data.items as Record<string, unknown>[]) || [data]

  if (items.length === 1) {
    const item = items[0]
    return <KpiCard item={item} />
  }

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: `repeat(${Math.min(items.length, 4)}, 1fr)`,
      gap: 16,
    }}>
      {items.map((item, idx) => (
        <KpiCard key={idx} item={item} />
      ))}
    </div>
  )
}

function KpiCard({ item }: { item: Record<string, unknown> }) {
  const title = String(item.title || item.label || '')
  const value = item.value ?? item.val ?? 0
  const displayValue = typeof value === 'number'
    ? (value % 1 !== 0 ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : value.toLocaleString())
    : String(value)
  const prefix = item.prefix ? String(item.prefix) : ''
  const suffix = item.suffix ? String(item.suffix) : ''
  const trendRaw = String(item.trend || item.compare || '')
  const trendVal = parseFloat(trendRaw.replace(/[^0-9.-]/g, ''))
  const isUp = trendRaw.includes('+') || trendRaw.includes('↑') || trendVal > 0
  const isDown = trendRaw.includes('-') || trendRaw.includes('↓') || trendVal < 0

  return (
    <Card
      size="small"
      styles={{ body: { padding: '20px 22px' } }}
      style={{
        borderRadius: 10,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
        transition: 'box-shadow 0.2s, transform 0.2s',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.boxShadow = '0 4px 6px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.06)'
        ;(e.currentTarget as HTMLElement).style.transform = 'translateY(-1px)'
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.boxShadow = '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)'
        ;(e.currentTarget as HTMLElement).style.transform = 'translateY(0)'
      }}
    >
      <Text style={{ fontSize: 12, color: '#64748B', display: 'block', marginBottom: 8, fontWeight: 500 }}>
        {title}
      </Text>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 26, fontWeight: 700, color: '#1E293B', lineHeight: 1.2 }}>
          {prefix}<span>{displayValue}</span>{suffix}
        </span>
        {trendRaw && (
          <span style={{
            fontSize: 12,
            fontWeight: 600,
            color: isUp ? '#059669' : isDown ? '#DC2626' : '#64748B',
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            background: isUp ? '#F0FDF4' : isDown ? '#FEF2F2' : '#F8FAFC',
            padding: '2px 8px',
            borderRadius: 12,
          }}>
            {isUp ? <ArrowUpOutlined /> : isDown ? <ArrowDownOutlined /> : <MinusOutlined />}
            {trendRaw.replace(/[↑↓]/g, '')}
          </span>
        )}
      </div>
    </Card>
  )
}