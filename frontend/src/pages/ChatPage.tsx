import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Form } from 'antd'
import {
  ReloadOutlined, SaveOutlined, DownloadOutlined, PlusOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { Text } from '../components/atelier/Typography'
import Button from '../components/atelier/Button'
import Modal from '../components/atelier/Modal'
import TextField from '../components/atelier/TextField'
import TextArea from '../components/atelier/TextArea'
import { useToast } from '../components/atelier/useToast'
import { useSessionStore } from '../stores/session'
import { exportReportHTML } from '../utils/export'
import AgentTimeline from '../components/chat/AgentTimeline'
import {
  IconReport, IconTemplate,
} from '../components/ui/Icons'
import ChatView from './views/ChatView'
import RunningView from './views/RunningView'
import ReportView from './views/ReportView'

export default function ChatPage() {
  const toast = useToast()
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
      toast.warning('当前没有可保存的报告')
      return
    }
    tmplForm.validateFields().then((values) => {
      saveAsTemplate(values.name, values.description ?? '')
      toast.success('已保存到模板中心')
      setTmplModalOpen(false)
      tmplForm.resetFields()
    })
  }

  const handleUseTemplate = (tmplId: string) => {
    const t = templates.find((x) => x.id === tmplId)
    if (!t) return
    setTemplateParams({ ...t.params })
    toast.success(`已套用模板「${t.name}」`)
    navigate('/')
  }

  useEffect(() => { fetchSessionsList() }, [fetchSessionsList])

  const showTimeline = viewMode === 'running'

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: 'var(--paper)' }}>
      <aside style={{
        width: 280,
        background: 'var(--canvas)',
        borderRight: '1px solid var(--line)',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}>
        <div style={{ padding: 12 }}>
          <Button
            variant="primary"
            block
            onClick={resetSession}
            disabled={busy}
            style={{ fontSize: 14, gap: 8, justifyContent: 'center' }}
          >
            <PlusOutlined style={{ fontSize: 14 }} /> 新建分析
          </Button>
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
              <Text style={{ fontSize: 12, color: 'var(--muted)', display: 'block' }}>暂无历史会话</Text>
              <Button
                size="sm"
                variant="quiet"
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
              size="sm"
              variant="quiet"
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
              <Text style={{ fontSize: 12, color: 'var(--muted)', display: 'block' }}>
                暂无模板
              </Text>
              <Button
                size="sm"
                variant="quiet"
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
              size="sm"
              variant="quiet"
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
          padding: '12px 16px', borderTop: '1px solid var(--line)',
          fontSize: 12, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span>Session: {sessionLabel}</span>
          <Button
            size="sm"
            variant="quiet"
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
            background: 'var(--paper)', padding: '8px 24px',
            borderBottom: '1px solid var(--line)',
            display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0,
          }}>
            <Text strong style={{ fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
              <IconReport style={{ color: 'var(--muted)' }} />当前报告
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, color: 'var(--muted)' }}>年份:</span>
              <select
                value={templateParams.year}
                onChange={(e) => setTemplateParams({ year: e.target.value })}
                style={{ fontSize: 13, padding: '2px 6px', borderRadius: 4, border: '1px solid var(--line)', background: 'var(--paper)', color: 'var(--ink)' }}
              >
                <option value="2024">2024</option>
                <option value="2025">2025</option>
              </select>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, color: 'var(--muted)' }}>区域:</span>
              <select
                value={templateParams.region}
                onChange={(e) => setTemplateParams({ region: e.target.value })}
                style={{ fontSize: 13, padding: '2px 6px', borderRadius: 4, border: '1px solid var(--line)', background: 'var(--paper)', color: 'var(--ink)' }}
              >
                <option value="华东区域">华东区域</option>
                <option value="华南区域">华南区域</option>
                <option value="全国">全国</option>
              </select>
            </div>
            <div style={{ flex: 1 }} />
            <Button
              size="sm"
              variant="default"
              style={{ fontSize: 13, gap: 4 }}
              onClick={() => setTmplModalOpen(true)}
              disabled={!currentReport || currentReport.blocks.length === 0}
            >
              <SaveOutlined /> 保存为模板
            </Button>
            <Button
              size="sm"
              variant="default"
              style={{ fontSize: 13, gap: 4 }}
              onClick={() => currentReport && exportReportHTML(currentReport)}
              disabled={!currentReport}
            >
              <DownloadOutlined /> 导出
            </Button>
            <Button size="sm" variant="default" style={{ fontSize: 13, gap: 4 }} disabled>
              <ReloadOutlined /> 刷新参数并重新生成
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
        onClose={() => setTmplModalOpen(false)}
        okText="保存"
        cancelText="取消"
      >
        <Form form={tmplForm} layout="vertical">
          <Form.Item name="name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
            <TextField placeholder="例如:华东月度销售趋势" />
          </Form.Item>
          <Form.Item name="description" label="描述(可选)">
            <TextArea rows={2} placeholder="模板用途说明" style={{ width: '100%' }} />
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
        fontSize: 13, fontWeight: 600, color: 'var(--muted)',
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
        background: active ? 'var(--canvas)' : 'transparent',
        border: '1px solid transparent',
        display: 'flex', alignItems: 'center', gap: 8,
        transition: 'color 0.15s, background 0.15s',
        fontSize: 14, color: active ? 'var(--ink)' : 'var(--muted)',
        fontWeight: active ? 600 : 400,
        opacity: disabled ? 0.55 : 1,
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.color = 'var(--ink)'
        if (!active) e.currentTarget.style.background = 'var(--paper)'
      }}
      onMouseLeave={(e) => {
        if (disabled) return
        e.currentTarget.style.color = active ? 'var(--ink)' : 'var(--muted)'
        if (!active) e.currentTarget.style.background = 'transparent'
      }}
    >
      <span style={{ display: 'flex', flexShrink: 0 }}>{icon}</span>
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      {meta && <span style={{ fontSize: 13, color: 'var(--muted)', flexShrink: 0 }}>{meta}</span>}
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
