import { Typography } from 'antd'
import { LoadingOutlined, CloseCircleFilled } from '@ant-design/icons'
import type { TimelineEntry } from '../../types/report'

const { Text } = Typography

interface Props {
  events: TimelineEntry[]
  isStreaming: boolean
}

const STATUS_ICONS: Record<string, string> = {
  success: '●',
  running: '●',
  pending: '○',
  error: '✕',
}
const STATUS_COLORS: Record<string, string> = {
  success: '#059669',
  running: '#D97706',
  pending: '#CBD5E1',
  error: '#DC2626',
}

export default function AgentTimeline({ events, isStreaming }: Props) {
  return (
    <div style={{
      width: 300,
      background: '#FFFFFF',
      borderLeft: '1px solid #E2E8F0',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      {/* Header */}
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
          <span style={{
            display: 'flex', alignItems: 'center', gap: 4,
            fontSize: 11, color: '#059669',
          }}>
            <span style={{ width: 6, height: 6, borderRadius: 3, background: '#059669', display: 'inline-block' }} />
            Live SSE
          </span>
        )}
      </div>

      {/* Body — custom timeline */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {events.length === 0 ? (
          <div style={{ textAlign: 'center', paddingTop: 40 }}>
            {isStreaming ? (
              <>
                <LoadingOutlined style={{ fontSize: 20, color: '#3B82F6' }} />
                <Text style={{ display: 'block', marginTop: 8, color: '#94A3B8', fontSize: 12 }}>
                  等待 Agent 启动...
                </Text>
              </>
            ) : (
              <Text style={{ color: '#CBD5E1', fontSize: 12 }}>暂无执行记录</Text>
            )}
          </div>
        ) : (
          events.map((event, idx) => {
            const isLast = idx === events.length - 1
            const color = STATUS_COLORS[event.status] || STATUS_COLORS.pending
            const icon = event.status === 'running'
              ? <LoadingOutlined style={{ color, fontSize: 12 }} />
              : event.status === 'error'
                ? <CloseCircleFilled style={{ color, fontSize: 12 }} />
                : null

            return (
              <div key={event.id} style={{
                position: 'relative',
                paddingLeft: 20,
                paddingBottom: isLast ? 4 : 14,
              }}>
                {/* Vertical line */}
                {!isLast && (
                  <div style={{
                    position: 'absolute', left: 6, top: 18, bottom: 0,
                    width: 2, background: '#E2E8F0',
                  }} />
                )}
                {/* Dot / indicator */}
                <div style={{
                  position: 'absolute', left: 0, top: 2,
                  width: 14, height: 14,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {icon || (
                    <span style={{
                      fontSize: 10,
                      color,
                      lineHeight: 1,
                    }}>
                      {STATUS_ICONS[event.status] || STATUS_ICONS.pending}
                    </span>
                  )}
                </div>
                {/* Content */}
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Text style={{
                      fontSize: 13,
                      color: event.status === 'running' ? '#1E40AF'
                        : event.status === 'pending' ? '#CBD5E1' : '#1E293B',
                      fontWeight: event.status === 'running' ? 500 : 400,
                    }}>
                      {event.nodeName}
                    </Text>
                    {event.status === 'running' && (
                      <span style={{
                        fontSize: 10, color: '#D97706', background: '#FEF3C7',
                        padding: '1px 6px', borderRadius: 4,
                      }}>
                        进行中
                      </span>
                    )}
                    {event.duration && (
                      <span style={{ fontSize: 10, color: '#94A3B8', marginLeft: 'auto' }}>
                        {event.duration}
                      </span>
                    )}
                  </div>
                  {event.detail && (
                    <Text style={{ display: 'block', fontSize: 11, color: '#94A3B8', marginTop: 2 }}>
                      {event.detail}
                    </Text>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}