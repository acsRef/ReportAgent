import { useEffect } from 'react'
import { IconChevronRight, IconClock, IconFile, IconHistory, IconMessage } from '../../components/ui/Icons'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '../stores/session'
import Empty from '../../components/atelier/Empty'
import Tag from '../../components/atelier/Tag'
import { Text, Title } from '../../components/atelier/Typography'
import '../../styles/observability.css'

type Tone = 'green' | 'red' | 'amber' | 'default'

function phaseMeta(phase: string): { label: string; tone: Tone } {
  if (phase === 'report_ready') return { label: '已完成', tone: 'green' }
  if (phase === 'error') return { label: '出错', tone: 'red' }
  if (phase === 'generating' || phase === 'adjusting' || phase === 'parsing') return { label: '进行中', tone: 'amber' }
  if (phase === 'awaiting_missing' || phase === 'awaiting_confirm') return { label: '待确认', tone: 'default' }
  return { label: phase || '—', tone: 'default' }
}

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
        <div className="obs-fade" style={{ marginBottom: 24 }}>
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {sessions.map((item, i) => {
              const pm = phaseMeta(item.phase ?? '')
              return (
                <div
                  key={item.session_id}
                  className="obs-fade obs-lift"
                  onClick={() => handleClick(item.session_id)}
                  style={{
                    animationDelay: `${Math.min(i, 8) * 45}ms`,
                    cursor: 'pointer',
                    background: 'var(--paper)',
                    borderRadius: 10,
                    padding: '16px 20px',
                    border: '1px solid var(--line)',
                    borderLeft: '3px solid var(--teal)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 14,
                  }}
                >
                  <span
                    style={{
                      width: 38, height: 38, borderRadius: 10, flexShrink: 0,
                      background: 'var(--teal-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}
                  >
                    <IconFile style={{ color: 'var(--teal-deep)', width: 18, height: 18 }} />
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <Text strong style={{ fontSize: 14, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.title || item.first_message || '(未命名会话)'}
                      </Text>
                      <Tag tone={pm.tone}>{pm.label}</Tag>
                    </div>
                    <div style={{ marginTop: 6, display: 'flex', gap: 16, alignItems: 'center' }}>
                      <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                        <IconMessage style={{ marginRight: 4 }} />{item.msg_count} 条消息
                      </span>
                      <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                        <IconClock style={{ marginRight: 4 }} />{new Date(item.last_message).toLocaleString('zh-CN')}
                      </span>
                    </div>
                  </div>
                  <IconChevronRight style={{ color: 'var(--faint)', width: 16, height: 16, flexShrink: 0 }} />
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
