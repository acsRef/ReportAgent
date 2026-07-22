import { Card, Typography, Row, Col } from 'antd'
import {
  FundViewOutlined,
  RiseOutlined,
  TeamOutlined,
  ShoppingCartOutlined,
} from '@ant-design/icons'

const { Text, Title } = Typography

const EXAMPLES = [
  { icon: <FundViewOutlined style={{ fontSize: 24, color: '#1E40AF' }} />, label: '区域销售分析', query: '2024年各区域的销售总额' },
  { icon: <RiseOutlined style={{ fontSize: 24, color: '#059669' }} />, label: '销售趋势分析', query: '最近6个月各月销售趋势' },
  { icon: <TeamOutlined style={{ fontSize: 24, color: '#D97706' }} />, label: '客户维度分析', query: '各等级客户的购买力对比' },
  { icon: <ShoppingCartOutlined style={{ fontSize: 24, color: '#DC2626' }} />, label: '退货分析', query: '2024年各品类退货率分析' },
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
        background: 'linear-gradient(135deg, #EFF6FF, #DBEAFE)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        margin: '0 auto 16px',
      }}>
        <span style={{ fontSize: 32 }}>📊</span>
      </div>
      <Title level={3} style={{ margin: 0, color: '#1E293B' }}>
        开始你的数据分析
      </Title>
      <Text style={{ display: 'block', marginTop: 8, fontSize: 14, lineHeight: 1.8, color: '#64748B' }}>
        输入分析问题，AI 将自动查询数据、生成分析报告
      </Text>

      <div style={{ marginTop: 40 }}>
        <Text style={{ fontSize: 12, color: '#94A3B8', display: 'block', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          快速开始
        </Text>
        <Row gutter={[12, 12]}>
          {EXAMPLES.map((ex) => (
            <Col span={12} key={ex.query}>
              <Card
                hoverable
                size="small"
                onClick={() => onExampleClick(ex.query)}
                style={{
                  textAlign: 'left', cursor: 'pointer', borderRadius: 10,
                  border: '1px solid #E2E8F0', transition: 'box-shadow 0.2s, transform 0.2s',
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.boxShadow = '0 4px 6px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.06)'
                  ;(e.currentTarget as HTMLElement).style.transform = 'translateY(-2px)'
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.boxShadow = '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)'
                  ;(e.currentTarget as HTMLElement).style.transform = 'translateY(0)'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {ex.icon}
                  <div>
                    <Text strong style={{ fontSize: 13, color: '#1E293B' }}>{ex.label}</Text>
                    <br />
                    <Text style={{ fontSize: 11, color: '#94A3B8' }}>{ex.query}</Text>
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </div>
    </div>
  )
}