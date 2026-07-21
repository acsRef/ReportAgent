import { Typography, Empty, Button } from 'antd'
import { AppstoreOutlined } from '@ant-design/icons'

const { Text, Title } = Typography

export default function TemplateCenter() {
  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f5f6f9',
      }}
    >
      <Empty
        image={<AppstoreOutlined style={{ fontSize: 48, color: '#bbb' }} />}
        description={
          <div style={{ textAlign: 'center' }}>
            <Title level={4} style={{ margin: '12px 0 4px' }}>
              模板中心
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              保存和管理你的报告模板，快速复用分析逻辑
            </Text>
          </div>
        }
      >
        <Button type="primary" disabled>
          新建模板
        </Button>
      </Empty>
    </div>
  )
}
