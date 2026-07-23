import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Typography, Select, Button, Modal, Form, Input, message } from 'antd'
import {
  ReloadOutlined, SaveOutlined, DownloadOutlined, PlusOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { useSessionStore } from '../stores/session'
import { exportReportHTML } from '../utils/export'
import AgentTimeline from '../components/chat/AgentTimeline'
import {
  IconReport, IconTemplate,
} from '../components/ui/Icons'
import ChatView from './views/ChatView'
import RunningView from './views/RunningView'
import ReportView from './views/ReportView'

const { Text } = Typography

export default function ChatPage() {
  const {
    viewMode, sessionId, sessions, currentReport, sessionLabel, timeline,
    templates, templateParams, setTemplateParams, resetSession, busy,
    fetchSessionsList, loadConversation, saveAsTemplate,
  } = useSessionStore()
  const navigate = useNavigate()
  const [tmplModalOpen, setTmplModalOpen] = useState(false)
  const [tmplForm] = Form.useForm()

  const handleSaveAsTemplate = () => {
    if (!currentReport || currentReport.blocks.length === 0) {
      message.warning('当前没有可保存的报告')
      return
    }
    tmplForm.validateFields().then((values) => {
      saveAsTemplate(values.name, values.description ?? '')
      message.success('已保存到模板中心')
      setTmplModalOpen(false)
      tmplForm.resetFields()
    })
  }

  const handleUseTemplate = (tmplId: string) => {
    const t = templates.find((x) => x.id === tmplId)
    if (!t) return
    setTemplateParams({ ...t.params })
    message.success(`已套用模板「${t.name}」`)
    navigate('/')
  }

  useEffect(() => { fetchSessionsList() }, [fetchSessionsList])

  const showTimeline = viewMode === 'running'

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: '#FFFFFF' }}>
      <aside style={{
        width: 280,
        background: '#FAFAFA',
        borderRight: '1px solid #E5E7EB',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}>
        <div style={{ padding: 12 }}>
          <button
            onClick={resetSession}
            disabled={busy}
            style={{
              width: '100%', padding: 8, background: '#1677FF', color: '#FFFFFF',
              border: 'none', borderRadius: 6, cursor: busy ? 'not-allowed' : 'pointer', fontSize: 14,
              opacity: busy ? 0.55 : 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              fontWeight: 500, boxShadow: 'none',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#4096FF' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = '#1677FF' }}
          >
            <PlusOutlined style={{ fontSize: 14 }} /> 新建分析
          </button>
        </div>

        <SidebarSection title="最近报告" icon={<IconReport />}>
          {sessions.slice(0, 5).map((s) => (
            <SidebarItem
              key={s.session_id} active={sessionId === s.session_id}
              icon={<IconReport />} label={s.first_message || '会话'}
              meta={formatRelativeTime(s.last_message)}
              onClick={() => { if (!busy) void loadConversation(s.session_id) }}
              disabled={busy}
            />
          ))}
          {sessions.length === 0 && (
            <div style={{ padding: '8px 4px' }}>
              <Text style={{ fontSize: 12, color: '#9CA3AF', display: 'block' }}>暂无历史会话</Text>
              <Button
                size="small"
                type="link"
                style={{ padding: '4px 0', height: 'auto', fontSize: 12 }}
                onClick={() => !busy && navigate('/history')}
                disabled={busy}
              >
                前往历史记录 →
              </Button>
            </div>
          )}
          {sessions.length > 5 && (
            <Button
              size="small"
              type="link"
              style={{ padding: '4px 0', height: 'auto', fontSize: 12 }}
              onClick={() => !busy && navigate('/history')}
              disabled={busy}
            >
              查看全部 →
            </Button>
          )}
        </SidebarSection>

        <SidebarSection title="模板中心" icon={<IconTemplate />}>
          {templates.slice(0, 4).map((t) => (
            <SidebarItem
              key={t.id}
              icon={<IconTemplate />}
              label={t.name}
              active={false}
              onClick={() => { if (!busy) handleUseTemplate(t.id) }}
              disabled={busy}
            />
          ))}
          {templates.length === 0 && (
            <div style={{ padding: '8px 4px' }}>
              <Text style={{ fontSize: 12, color: '#9CA3AF', display: 'block' }}>
                暂无模板
              </Text>
              <Button
                size="small"
                type="link"
                style={{ padding: '4px 0', height: 'auto', fontSize: 12 }}
                onClick={() => !busy && navigate('/templates')}
                disabled={busy}
              >
                前往模板中心 →
              </Button>
            </div>
          )}
          {templates.length > 0 && (
            <Button
              size="small"
              type="link"
              style={{ padding: '4px 0', height: 'auto', fontSize: 12 }}
              onClick={() => !busy && navigate('/templates')}
              disabled={busy}
            >
              管理全部模板 →
            </Button>
          )}
        </SidebarSection>

        <div style={{ flex: 1 }} />

        <div style={{
          padding: '12px 16px', borderTop: '1px solid #F3F4F6',
          fontSize: 12, color: '#9CA3AF', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span>Session: {sessionLabel}</span>
          <Button
            size="small"
            type="link"
            style={{ padding: 0, height: 'auto', fontSize: 12 }}
            onClick={() => !busy && navigate('/history')}
            disabled={busy}
          >
            历史记录
          </Button>
        </div>
      </aside>

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {viewMode === 'report' && (
          <div style={{
            background: '#FFFFFF', padding: '8px 24px',
            borderBottom: '1px solid #E5E7EB',
            display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0,
          }}>
            <Text strong style={{ fontSize: 14, color: '#0F172A', display: 'flex', alignItems: 'center', gap: 8 }}>
              <IconReport style={{ color: '#6B7280' }} />当前报告
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, color: '#6B7280' }}>年份:</span>
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
              <span style={{ fontSize: 13, color: '#6B7280' }}>区域:</span>
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
            <Button
              size="small"
              icon={<SaveOutlined />}
              style={{ fontSize: 13, borderRadius: 6 }}
              onClick={() => setTmplModalOpen(true)}
              disabled={!currentReport || currentReport.blocks.length === 0}
            >
              保存为模板
            </Button>
            <Button
              size="small"
              icon={<DownloadOutlined />}
              style={{ fontSize: 13, borderRadius: 6 }}
              onClick={() => currentReport && exportReportHTML(currentReport)}
              disabled={!currentReport}
            >
              导出
            </Button>
            <Button size="small" icon={<ReloadOutlined />} style={{ fontSize: 13, borderRadius: 6 }} disabled>
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

      {showTimeline && <AgentTimeline events={timeline} isStreaming={true} />}

      <Modal
        title="保存为模板"
        open={tmplModalOpen}
        onOk={handleSaveAsTemplate}
        onCancel={() => setTmplModalOpen(false)}
        okText="保存"
        cancelText="取消"
      >
        <Form form={tmplForm} layout="vertical">
          <Form.Item name="name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
            <Input placeholder="例如:华东月度销售趋势" />
          </Form.Item>
          <Form.Item name="description" label="描述(可选)">
            <Input.TextArea rows={2} placeholder="模板用途说明" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

interface SidebarSectionProps {
  title: string
  icon: ReactNode
  children: ReactNode
}

function SidebarSection({ title, icon, children }: SidebarSectionProps) {
  return (
    <div style={{ padding: 12 }}>
      <Text style={{
        fontSize: 13, fontWeight: 600, color: '#6B7280',
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8,
      }}>
        {icon}{title}
      </Text>
      {children}
    </div>
  )
}

interface SidebarItemProps {
  icon: ReactNode
  label: string
  meta?: string
  active?: boolean
  onClick: () => void
  disabled?: boolean
}

function SidebarItem({ icon, label, meta, active, onClick, disabled = false }: SidebarItemProps) {
  return (
    <div
      onClick={() => { if (!disabled) onClick() }}
      style={{
        padding: '8px 12px', borderRadius: 6, cursor: disabled ? 'not-allowed' : 'pointer', marginBottom: 4,
        background: active ? '#EFF6FF' : 'transparent',
        border: '1px solid transparent',
        display: 'flex', alignItems: 'center', gap: 8,
        transition: 'color 0.15s, background 0.15s',
        fontSize: 14, color: active ? '#0F172A' : '#6B7280',
        fontWeight: active ? 600 : 400,
        opacity: disabled ? 0.55 : 1,
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.color = '#0F172A'
        if (!active) e.currentTarget.style.background = '#FFFFFF'
      }}
      onMouseLeave={(e) => {
        if (disabled) return
        e.currentTarget.style.color = active ? '#0F172A' : '#6B7280'
        if (!active) e.currentTarget.style.background = 'transparent'
      }}
    >
      <span style={{ display: 'flex', flexShrink: 0 }}>{icon}</span>
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      {meta && <span style={{ fontSize: 13, color: '#9CA3AF', flexShrink: 0 }}>{meta}</span>}
    </div>
  )
}

function formatRelativeTime(dateStr: string): string {
  try {
    const d = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffHrs = Math.floor(diffMs / (1000 * 60 * 60))
    if (diffHrs < 1) return '刚刚'
    if (diffHrs < 24) return `${diffHrs}h前`
    return `${Math.floor(diffHrs / 24)}天前`
  } catch { return '' }
}
