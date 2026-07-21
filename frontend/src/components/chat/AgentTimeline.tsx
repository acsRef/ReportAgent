import { Typography } from 'antd'
import { CheckCircleFilled, LoadingOutlined, MinusCircleOutlined } from '@ant-design/icons'
import type { TimelineEntry } from '../../types/report'

const { Text } = Typography

interface Props {
  events: TimelineEntry[]
  isStreaming: boolean
}

const STATUS_ICONS: Record<string, React.ReactNode> = {
  success: <CheckCircleFilled style={{ color: '#1677ff', fontSize: 12 }} />,
  running: <LoadingOutlined style={{ color: '#faad14', fontSize: 12 }} />,
  pending: <MinusCircleOutlined style={{ color: '#bbb', fontSize: 12 }} />,
  error: <span style={{ color: '#ff4d4f', fontSize: 12 }}>✕</span>,
}

export default function AgentTimeline({ events, isStreaming }: Props) {
  if (events.length === 0 && !isStreaming) {
    return (
      <div
        style={{
          height: 200,
          background: '#fafbfc',
          borderTop: '1px solid #e8e8e8',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{
          padding: '10px 16px',
          borderBottom: '1px solid #f0f0f0',
          background: '#fafafa',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexShrink: 0,
        }}>
          <Text strong style={{ fontSize: 13, color: '#555' }}>
            ⚙️ Agent Runtime
          </Text>
        </div>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>暂无执行记录</Text>
        </div>
      </div>
    )
  }

  return (
    <div
      style={{
        height: 260,
        background: '#fafbfc',
        borderTop: '1px solid #e8e8e8',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}
    >
      <div style={{
        padding: '10px 16px',
        borderBottom: '1px solid #f0f0f0',
        background: '#fafafa',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <Text strong style={{ fontSize: 13, color: '#555' }}>
          ⚙️ Agent Runtime
        </Text>
        {isStreaming && (
          <span style={{ fontSize: 11, color: '#52c41a', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: 3, background: '#52c41a', display: 'inline-block' }} />
            Live SSE
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 20px' }}>
        {events.length === 0 && isStreaming && (
          <div style={{ textAlign: 'center', paddingTop: 20 }}>
            <LoadingOutlined style={{ fontSize: 20, color: '#faad14' }} />
            <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
              等待 Agent 启动...
            </Text>
          </div>
        )}

        {events.map((event, idx) => {
          const isLast = idx === events.length - 1
          return (
            <div
              key={event.id}
              style={{
                position: 'relative',
                paddingLeft: 24,
                paddingBottom: isLast ? 4 : 16,
              }}
            >
              {/* Vertical line */}
              {!isLast && (
                <div
                  style={{
                    position: 'absolute',
                    left: 7,
                    top: 18,
                    bottom: 0,
                    width: 2,
                    background: '#e0e0e0',
                  }}
                />
              )}

              {/* Icon */}
              <div
                style={{
                  position: 'absolute',
                  left: 0,
                  top: 2,
                  width: 16,
                  height: 16,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {STATUS_ICONS[event.status] || STATUS_ICONS.pending}
              </div>

              {/* Content */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text
                  style={{
                    fontSize: 13,
                    fontWeight: event.status === 'running' ? 500 : 400,
                    color: event.status === 'running' ? '#1677ff' : event.status === 'pending' ? '#bbb' : '#333',
                  }}
                >
                  {event.nodeName}
                </Text>
                {event.duration && (
                  <Text style={{ fontSize: 11, color: '#999' }}>
                    {event.duration}
                  </Text>
                )}
                {event.status === 'running' && (
                  <Text style={{ fontSize: 11, color: '#faad14' }}>
                    执行中...
                  </Text>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
