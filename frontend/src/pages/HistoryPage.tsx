import { useEffect } from 'react'
import { IconHistory, IconFile, IconClock, IconMessage } from '../components/ui/Icons'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '../stores/session'
import Empty from '../components/atelier/Empty'
import { Text, Title } from '../components/atelier/Typography'

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
        background: 'var(--canvas)',
        padding: 32,
        overflow: 'auto',
      }}
    >
      <div style={{ maxWidth: 800, margin: '0 auto', width: '100%' }}>
        <div style={{ marginBottom: 24 }}>
          <Title level={4} style={{ margin: 0, color: 'var(--ink)' }}>
            <IconHistory style={{ marginRight: 8 }} />
            历史报告
          </Title>
          <Text type="secondary" style={{ fontSize: 13, color: 'var(--muted)' }}>
            查看和管理此前生成的报告
          </Text>
        </div>

        {sessions.length === 0 ? (
          <Empty
            description={<span style={{ fontSize: 13, color: 'var(--muted)' }}>暂无历史报告</span>}
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
                  background: 'var(--paper)',
                  borderRadius: 8,
                  padding: '16px 20px',
                  border: '1px solid var(--line)',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 12,
                }}
              >
                <IconFile style={{ color: 'var(--teal)', width: 18, height: 18, marginTop: 2 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Text strong style={{ fontSize: 14, display: 'block', color: 'var(--ink)' }}>
                    {item.first_message}
                  </Text>
                  <div style={{ marginTop: 6, display: 'flex', gap: 16, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                      <IconMessage style={{ marginRight: 4 }} />{item.msg_count} 条消息
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                      <IconClock style={{ marginRight: 4 }} />{new Date(item.last_message).toLocaleString('zh-CN')}
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
