import { Typography, List, Empty, Tag } from 'antd'
import { HistoryOutlined, FileTextOutlined, ClockCircleOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '../stores/session'

const { Text, Title } = Typography

export default function HistoryPage() {
  const { reports } = useSessionStore()
  const navigate = useNavigate()

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: '#f5f6f9',
        padding: 32,
        overflow: 'auto',
      }}
    >
      <div style={{ maxWidth: 800, margin: '0 auto', width: '100%' }}>
        <div style={{ marginBottom: 24 }}>
          <Title level={4} style={{ margin: 0 }}>
            <HistoryOutlined style={{ marginRight: 8 }} />
            历史报告
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            查看和管理此前生成的报告
          </Text>
        </div>

        {reports.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text style={{ fontSize: 13 }}>暂无历史报告</Text>}
            style={{ marginTop: 60 }}
          />
        ) : (
          <List
            dataSource={[...reports].reverse()}
            renderItem={(item) => (
              <List.Item
                onClick={() => navigate('/')}
                style={{
                  cursor: 'pointer',
                  background: '#fff',
                  borderRadius: 8,
                  padding: '16px 20px',
                  marginBottom: 8,
                  border: '1px solid #e8e8e8',
                  transition: 'box-shadow 0.2s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, width: '100%' }}>
                  <FileTextOutlined style={{ color: '#1677ff', fontSize: 18, marginTop: 2 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Text strong style={{ fontSize: 14, display: 'block' }}>
                      {item.query}
                    </Text>
                    <div style={{ marginTop: 6, display: 'flex', gap: 8, alignItems: 'center' }}>
                      <ClockCircleOutlined style={{ fontSize: 11, color: '#999' }} />
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {new Date(item.timestamp).toLocaleString('zh-CN')}
                      </Text>
                      <Tag color={item.status === 'done' ? 'success' : 'processing'} style={{ fontSize: 10 }}>
                        {item.status === 'done' ? '已完成' : '生成中'}
                      </Tag>
                    </div>
                  </div>
                </div>
              </List.Item>
            )}
          />
        )}
      </div>
    </div>
  )
}
