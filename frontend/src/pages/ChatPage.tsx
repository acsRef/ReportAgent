import { useEffect } from 'react'
import { Typography, Select, Button } from 'antd'
import {
  ReloadOutlined, FileTextOutlined, SaveOutlined, DownloadOutlined,
  ClockCircleOutlined, PlusOutlined, HistoryOutlined, MessageOutlined,
} from '@ant-design/icons'
import { useSessionStore } from '../stores/session'
import { exportReportHTML } from '../utils/export'
import AgentTimeline from '../components/chat/AgentTimeline'
import ChatView from './views/ChatView'
import RunningView from './views/RunningView'
import ReportView from './views/ReportView'

const { Text } = Typography

export default function ChatPage() {
  const {
    viewMode, sessionId, sessions, currentReport, sessionLabel, timeline,
    templateParams, setTemplateParams, resetSession,
    fetchSessionsList, loadConversation,
  } = useSessionStore()

  useEffect(() => { fetchSessionsList() }, [fetchSessionsList])

  const showTimeline = viewMode === 'running'

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: '#F8FAFC' }}>
      {/* Left Sidebar — dark, matches nav */}
      <aside style={{
        width: 280, background: '#0F172A', display: 'flex', flexDirection: 'column', flexShrink: 0,
      }}>
        <div style={{
          padding: '14px 16px', borderBottom: '1px solid #1E293B',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <Text style={{ fontSize: 12, fontWeight: 600, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            <HistoryOutlined style={{ marginRight: 6 }} />
            历史查询
          </Text>
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={resetSession}
            style={{ background: 'transparent', borderColor: '#334155', color: '#94A3B8', fontSize: 12, borderRadius: 6 }}
          >
            新建
          </Button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 8px' }}>
          {sessions.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center' }}>
              <MessageOutlined style={{ fontSize: 24, color: '#334155', marginBottom: 8 }} />
              <Text style={{ fontSize: 12, color: '#475569', display: 'block' }}>暂无查询记录</Text>
            </div>
          ) : (
            sessions.map((s) => {
              const isActive = sessionId === s.session_id
              return (
                <div
                  key={s.session_id}
                  onClick={() => loadConversation(s.session_id)}
                  style={{
                    padding: '10px 12px', borderRadius: 8, cursor: 'pointer', marginBottom: 2,
                    background: isActive ? 'rgba(59,130,246,0.12)' : 'transparent',
                    border: isActive ? '1px solid rgba(59,130,246,0.3)' : '1px solid transparent',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)' }}
                  onMouseLeave={(e) => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                >
                  <div style={{
                    fontSize: 13, fontWeight: isActive ? 500 : 400,
                    color: isActive ? '#F1F5F9' : '#94A3B8',
                    marginBottom: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {s.first_message}
                  </div>
                  <div style={{ fontSize: 11, color: '#475569', display: 'flex', gap: 12 }}>
                    <span>{s.msg_count} 条消息</span>
                    <span>
                      <ClockCircleOutlined style={{ marginRight: 4 }} />
                      {new Date(s.last_message).toLocaleString('zh-CN')}
                    </span>
                  </div>
                </div>
              )
            })
          )}
        </div>
        <div style={{
          padding: '10px 16px', borderTop: '1px solid #1E293B',
          fontSize: 11, color: '#475569',
        }}>
          Session: {sessionLabel}
        </div>
      </aside>

      {/* Center — view container */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {viewMode === 'report' && (
          <div style={{
            background: '#FFFFFF', padding: '6px 24px',
            borderBottom: '1px solid #E2E8F0',
            display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0,
          }}>
            <Text strong style={{ fontSize: 13, color: '#1E293B' }}>
              <FileTextOutlined style={{ marginRight: 6, color: '#3B82F6' }} />
              当前报告
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: '#64748B' }}>年份:</span>
              <Select
                size="small"
                value={templateParams.year}
                onChange={(v) => setTemplateParams({ year: v })}
                style={{ width: 80 }}
                options={[
                  { value: '2024', label: '2024' },
                  { value: '2025', label: '2025' },
                ]}
              />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: '#64748B' }}>区域:</span>
              <Select
                size="small"
                value={templateParams.region}
                onChange={(v) => setTemplateParams({ region: v })}
                style={{ width: 100 }}
                options={[
                  { value: '华东区域', label: '华东区域' },
                  { value: '华南区域', label: '华南区域' },
                  { value: '全国', label: '全国' },
                ]}
              />
            </div>
            <div style={{ flex: 1 }} />
            <Button size="small" icon={<SaveOutlined />} style={{ fontSize: 12, borderRadius: 6 }}>
              保存为模板
            </Button>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              style={{ fontSize: 12, borderRadius: 6 }}
              onClick={() => currentReport && exportReportHTML(currentReport)}
              disabled={!currentReport}
            >
              导出
            </Button>
            <Button size="small" icon={<ReloadOutlined />} style={{ fontSize: 12, borderRadius: 6 }} disabled>
              刷新参数并重新生成
            </Button>
          </div>
        )}

        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          {viewMode === 'chat' && <ChatView />}
          {viewMode === 'running' && <RunningView />}
          {viewMode === 'report' && <ReportView />}
        </div>
      </main>

      {/* Right — Agent Runtime panel (shown during execution) */}
      {showTimeline && (
        <AgentTimeline events={timeline} isStreaming={true} />
      )}
    </div>
  )
}