import { Card, Typography, Row, Col } from 'antd'
import {
  FundViewOutlined,
  RiseOutlined,
  TeamOutlined,
  ShoppingCartOutlined,
} from '@ant-design/icons'

const { Text, Title } = Typography

const EXAMPLES = [
  { icon: <FundViewOutlined style={{ fontSize: 24, color: '#1677ff' }} />, label: '区域销售分析', query: '2024年各区域的销售总额' },
  { icon: <RiseOutlined style={{ fontSize: 24, color: '#52c41a' }} />, label: '销售趋势分析', query: '最近6个月各月销售趋势' },
  { icon: <TeamOutlined style={{ fontSize: 24, color: '#faad14' }} />, label: '客户维度分析', query: '各等级客户的购买力对比' },
  { icon: <ShoppingCartOutlined style={{ fontSize: 24, color: '#ff4d4f' }} />, label: '退货分析', query: '2024年各品类退货率分析' },
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
      <div style={{ fontSize: 48, marginBottom: 16 }}>📊</div>
      <Title level={3} style={{ margin: 0 }}>
        开始你的数据分析
      </Title>
      <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 14, lineHeight: 1.8 }}>
        输入分析问题，AI 将自动查询数据、生成分析报告
      </Text>

      <div style={{ marginTop: 40 }}>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
          快速开始
        </Text>
        <Row gutter={[12, 12]}>
          {EXAMPLES.map((ex) => (
            <Col span={12} key={ex.query}>
              <Card
                hoverable
                size="small"
                onClick={() => onExampleClick(ex.query)}
                style={{ textAlign: 'left', cursor: 'pointer' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {ex.icon}
                  <div>
                    <Text strong style={{ fontSize: 13 }}>{ex.label}</Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: 11 }}>{ex.query}</Text>
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
