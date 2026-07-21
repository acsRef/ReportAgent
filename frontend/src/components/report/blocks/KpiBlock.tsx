import { Typography } from 'antd'
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
    <div className="kpi-grid" style={{
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
  const trendVal = parseFloat(trendRaw.replace(/[^0-9.\-]/g, ''))
  const isUp = trendRaw.includes('+') || trendRaw.includes('↑') || trendVal > 0
  const isDown = trendRaw.includes('-') || trendRaw.includes('↓') || trendVal < 0

  return (
    <div style={{
      background: '#fff',
      padding: '18px 20px',
      borderRadius: 8,
      border: '1px solid #e8e8e8',
      boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
    }}>
      <Text style={{ fontSize: 12, color: '#646a73', display: 'block', marginBottom: 8 }}>
        {title}
      </Text>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 26, fontWeight: 700, color: '#1f2329', lineHeight: 1.2 }}>
          {prefix}<span>{displayValue}</span>{suffix}
        </span>
        {trendRaw && (
          <span style={{
            fontSize: 12,
            fontWeight: 600,
            color: isUp ? '#52c41a' : isDown ? '#ff4d4f' : '#8f959e',
            display: 'flex',
            alignItems: 'center',
            gap: 2,
          }}>
            {isUp ? <ArrowUpOutlined /> : isDown ? <ArrowDownOutlined /> : <MinusOutlined />}
            {trendRaw.replace(/[↑↓]/g, '')}
          </span>
        )}
      </div>
    </div>
  )
}
