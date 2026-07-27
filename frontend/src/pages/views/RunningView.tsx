import { LoadingOutlined, CheckCircleFilled, MinusCircleOutlined } from '@ant-design/icons'
import { useSessionStore } from '../../stores/session'
import { Text } from '../../components/atelier/Typography'
import Spinner from '../../components/atelier/Spinner'

export default function RunningView() {
  const { timeline } = useSessionStore()

  const STATUS_ICONS: Record<string, React.ReactNode> = {
    success: <CheckCircleFilled style={{ color: '#059669', fontSize: 14 }} />,
    running: <LoadingOutlined style={{ color: '#D97706', fontSize: 14 }} />,
    pending: <MinusCircleOutlined style={{ color: 'var(--faint)', fontSize: 14 }} />,
  }

  const currentRunning = timeline.find(t => t.status === 'running')

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      background: 'var(--canvas)', gap: 28, padding: 40,
    }}>
      <Spinner />
      <div style={{ textAlign: 'center' }}>
        <Text style={{ fontSize: 17, fontWeight: 600, color: 'var(--ink)', display: 'block', marginBottom: 8 }}>
          Agent 正在执行分析
        </Text>
        <Text style={{ color: 'var(--muted)', fontSize: 13 }}>
          {currentRunning
            ? <span>当前步骤: <span style={{ color: 'var(--teal-deep)', fontWeight: 500 }}>{currentRunning.nodeName}</span></span>
            : '正在启动执行引擎...'}
        </Text>
      </div>

      {timeline.length > 0 && (
        <div style={{
          width: 420,
          marginTop: 8,
          background: 'var(--paper)',
          borderRadius: 10,
          border: '1px solid var(--line)',
          padding: '16px 20px',
          boxShadow: 'var(--shadow-card)',
        }}>
          <Text strong style={{ fontSize: 12, color: 'var(--muted)', display: 'block', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            执行步骤
          </Text>
          {timeline.map((event) => (
            <div
              key={event.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 0',
                borderBottom: '1px solid var(--line)',
              }}
            >
              {STATUS_ICONS[event.status] || STATUS_ICONS.pending}
              <div style={{ flex: 1 }}>
                <Text style={{
                  fontSize: 13,
                  color: event.status === 'running' ? 'var(--teal-deep)' : event.status === 'pending' ? 'var(--faint)' : 'var(--ink)',
                  fontWeight: event.status === 'running' ? 500 : 400,
                }}>
                  {event.nodeName}
                </Text>
              </div>
              {event.duration && (
                <Text style={{ fontSize: 11, color: 'var(--muted)' }}>{event.duration}</Text>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
