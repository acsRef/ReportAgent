import { IconLoading, IconCloseCircle } from '../../../components/ui/Icons'
import { Text } from '../../../components/atelier/Typography'
import type { TimelineEntry } from '../../../types/report'

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
      background: 'var(--paper)',
      borderLeft: '1px solid var(--line)',
      display: 'flex',
      flexDirection: 'column',
      flexShrink: 0,
    }}>
      <div style={{
        padding: '14px 16px',
        borderBottom: '1px solid var(--line)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <Text strong style={{ fontSize: 13, color: 'var(--ink)' }}>
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

      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {events.length === 0 ? (
          <div style={{ textAlign: 'center', paddingTop: 40 }}>
            {isStreaming ? (
              <>
                <IconLoading style={{ width: 20, height: 20, color: 'var(--teal)' }} />
                <Text style={{ display: 'block', marginTop: 8, color: 'var(--muted)', fontSize: 12 }}>
                  等待 Agent 启动...
                </Text>
              </>
            ) : (
              <Text style={{ color: 'var(--faint)', fontSize: 12 }}>暂无执行记录</Text>
            )}
          </div>
        ) : (
          events.map((event, idx) => {
            const isLast = idx === events.length - 1
            const color = STATUS_COLORS[event.status] || STATUS_COLORS.pending
            const icon = event.status === 'running'
              ? <IconLoading style={{ color, width: 12, height: 12 }} />
              : event.status === 'error'
                ? <IconCloseCircle style={{ color, width: 12, height: 12 }} />
                : null

            return (
              <div key={event.id} style={{
                position: 'relative',
                paddingLeft: 20,
                paddingBottom: isLast ? 4 : 14,
              }}>
                {!isLast && (
                  <div style={{
                    position: 'absolute', left: 6, top: 18, bottom: 0,
                    width: 2, background: 'var(--line)',
                  }} />
                )}
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
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Text style={{
                      fontSize: 13,
                      color: event.status === 'running' ? 'var(--teal-deep)'
                        : event.status === 'pending' ? 'var(--faint)' : 'var(--ink)',
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
                      <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 'auto' }}>
                        {event.duration}
                      </span>
                    )}
                  </div>
                  {event.detail && (
                    <Text style={{ display: 'block', fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
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
