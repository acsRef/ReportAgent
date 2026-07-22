import { Timeline, Typography, Tag } from 'antd'
import { CheckCircleFilled, LoadingOutlined, MinusCircleOutlined, CloseCircleFilled } from '@ant-design/icons'
import type { TimelineEntry } from '../../types/report'

const { Text } = Typography

interface Props {
  events: TimelineEntry[]
  isStreaming: boolean
}

const STATUS_CONFIG: Record<string, { color: string; dot: React.ReactNode }> = {
  success: { color: 'green', dot: <CheckCircleFilled style={{ color: '#059669', fontSize: 14 }} /> },
  running: { color: 'blue', dot: <LoadingOutlined style={{ color: '#D97706', fontSize: 14 }} /> },
  pending: { color: 'gray', dot: <MinusCircleOutlined style={{ color: '#CBD5E1', fontSize: 14 }} /> },
  error: { color: 'red', dot: <CloseCircleFilled style={{ color: '#DC2626', fontSize: 14 }} /> },
}

export default function AgentTimeline({ events, isStreaming }: Props) {
  const items = events.map((event) => {
    const cfg = STATUS_CONFIG[event.status] || STATUS_CONFIG.pending
    return {
      color: cfg.color,
      dot: cfg.dot,
      children: (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text style={{
            fontSize: 13,
            fontWeight: event.status === 'running' ? 500 : 400,
            color: event.status === 'running' ? '#1E40AF' : event.status === 'pending' ? '#CBD5E1' : '#1E293B',
          }}>
            {event.nodeName}
          </Text>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {event.status === 'running' && (
              <Tag color="warning" style={{ fontSize: 10, lineHeight: '18px', padding: '0 6px', borderRadius: 4 }}>
                执行中
              </Tag>
            )}
            {event.duration && (
              <Text style={{ fontSize: 11, color: '#94A3B8' }}>{event.duration}</Text>
            )}
          </div>
        </div>
      ),
    }
  })

  return (
    <div style={{
      width: 300,
      background: '#FFFFFF',
      borderLeft: '1px solid #E2E8F0',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      <div style={{
        padding: '14px 16px',
        borderBottom: '1px solid #F1F5F9',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <Text strong style={{ fontSize: 13, color: '#1E293B' }}>
          <span style={{ marginRight: 6 }}>⚙️</span>
          Agent Runtime
        </Text>
        {isStreaming && (
          <Tag color="default" style={{
            fontSize: 10,
            lineHeight: '18px',
            padding: '0 6px',
            borderRadius: 4,
            background: '#EFF6FF',
            border: '1px solid #DBEAFE',
            color: '#1E40AF',
          }}>
            SSE
          </Tag>
        )}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {events.length === 0 ? (
          <div style={{ textAlign: 'center', paddingTop: 24 }}>
            {isStreaming ? (
              <>
                <LoadingOutlined style={{ fontSize: 20, color: '#3B82F6' }} />
                <Text style={{ display: 'block', marginTop: 8, color: '#94A3B8', fontSize: 12 }}>等待 Agent 启动...</Text>
              </>
            ) : (
              <Text style={{ color: '#CBD5E1', fontSize: 12 }}>暂无执行记录</Text>
            )}
          </div>
        ) : (
          <Timeline items={items} />
        )}
      </div>
    </div>
  )
}