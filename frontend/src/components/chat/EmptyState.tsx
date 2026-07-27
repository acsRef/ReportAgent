import {
  IconFund,
  IconRise,
  IconTeam,
  IconCart,
} from '../ui/Icons'
import { Text, Title } from '../atelier/Typography'
import Card from '../atelier/Card'

const EXAMPLES = [
  { icon: <IconFund style={{ width: 24, height: 24, color: 'var(--teal-deep)' }} />, label: '区域销售分析', query: '2024年各区域的销售总额' },
  { icon: <IconRise style={{ width: 24, height: 24, color: '#059669' }} />, label: '销售趋势分析', query: '最近6个月各月销售趋势' },
  { icon: <IconTeam style={{ width: 24, height: 24, color: '#D97706' }} />, label: '客户维度分析', query: '各等级客户的购买力对比' },
  { icon: <IconCart style={{ width: 24, height: 24, color: '#DC2626' }} />, label: '退货分析', query: '2024年各品类退货率分析' },
]

interface Props {
  onExampleClick: (query: string) => void
}

export default function EmptyState({ onExampleClick }: Props) {
  return (
    <div
      style={{
        maxWidth: 640,
        margin: '0 auto',
        textAlign: 'center',
        paddingTop: 80,
      }}
    >
      <div style={{
        width: 64, height: 64, borderRadius: 16,
        background: 'linear-gradient(135deg, var(--teal-soft), var(--teal-pale))',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        margin: '0 auto 16px',
      }}>
        <span style={{ fontSize: 32 }}>📊</span>
      </div>
      <Title level={3} style={{ margin: 0, color: 'var(--ink)' }}>
        开始你的数据分析
      </Title>
      <Text style={{ display: 'block', marginTop: 8, fontSize: 14, lineHeight: 1.8, color: 'var(--muted)' }}>
        输入分析问题，AI 将自动查询数据、生成分析报告
      </Text>

      <div style={{ marginTop: 40 }}>
        <Text style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          快速开始
        </Text>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {EXAMPLES.map((ex) => (
            <Card
              key={ex.query}
              hoverable
              size="small"
              onClick={() => onExampleClick(ex.query)}
              style={{
                textAlign: 'left', cursor: 'pointer', borderRadius: 10,
                border: '1px solid var(--line)', transition: 'box-shadow 0.2s, transform 0.2s',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {ex.icon}
                <div>
                  <Text strong style={{ fontSize: 13, color: 'var(--ink)' }}>{ex.label}</Text>
                  <br />
                  <Text style={{ fontSize: 11, color: 'var(--muted)' }}>{ex.query}</Text>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  )
}
