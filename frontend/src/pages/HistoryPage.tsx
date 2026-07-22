import { useEffect } from 'react'
import { Typography, Empty } from 'antd'
import { HistoryOutlined, FileTextOutlined, ClockCircleOutlined, MessageOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '../stores/session'

const { Text, Title } = Typography

export default function HistoryPage() {
  const { sessions, fetchSessionsList, loadConversation } = useSessionStore()
  const navigate = useNavigate()

  useEffect(() => { fetchSessionsList() }, [fetchSessionsList])

  const handleClick = (sessionId: string) => {
    loadConversation(sessionId)
    navigate('/')
  }

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

        {sessions.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text style={{ fontSize: 13 }}>暂无历史报告</Text>}
            style={{ marginTop: 60 }}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sessions.map((item) => (
              <div
                key={item.session_id}
                onClick={() => handleClick(item.session_id)}
                style={{
                  cursor: 'pointer',
                  background: '#fff',
                  borderRadius: 8,
                  padding: '16px 20px',
                  border: '1px solid #e8e8e8',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 12,
                }}
              >
                <FileTextOutlined style={{ color: '#1677ff', fontSize: 18, marginTop: 2 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Text strong style={{ fontSize: 14, display: 'block' }}>
                    {item.first_message}
                  </Text>
                  <div style={{ marginTop: 6, display: 'flex', gap: 16, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: '#999' }}>
                      <MessageOutlined style={{ marginRight: 4 }} />{item.msg_count} 条消息
                    </span>
                    <span style={{ fontSize: 11, color: '#999' }}>
                      <ClockCircleOutlined style={{ marginRight: 4 }} />{new Date(item.last_message).toLocaleString('zh-CN')}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
