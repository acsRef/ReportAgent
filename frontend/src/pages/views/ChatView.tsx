import { useState, useCallback, useRef, useEffect } from 'react'
import { Typography, Input, Button, Spin } from 'antd'
import { SendOutlined, UserOutlined, RobotOutlined, CheckCircleOutlined, LoadingOutlined, FileTextOutlined } from '@ant-design/icons'
import { useSessionStore } from '../../stores/session'
import { adaptReport } from '../../adapter/reportAdapter'
import EmptyState from '../../components/chat/EmptyState'
import type { ConversationMessage, ReportEntry } from '../../types/report'
import { v4 as uuid } from 'uuid'

const { Text } = Typography
const { TextArea } = Input

const styles: Record<string, React.CSSProperties> = {
  messageBubble: {
    padding: '10px 14px',
    borderRadius: 10,
    fontSize: 13,
    lineHeight: 1.6,
    boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
  },
}

export default function ChatView() {
  const {
    currentReport, isStreaming, timeline, error, chatMessages,
    sendMessage, setViewMode,
  } = useSessionStore()

  const [inputText, setInputText] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [currentReport?.content, currentReport?.steps, chatMessages])

  const handleSend = useCallback(() => {
    const trimmed = inputText.trim()
    if (!trimmed || isStreaming) return
    sendMessage(trimmed)
    setInputText('')
  }, [inputText, isStreaming, sendMessage])

  const handleExample = useCallback((query: string) => {
    sendMessage(query)
  }, [sendMessage])

  const viewReport = useCallback((reportData: Record<string, unknown>, queryText: string) => {
    const resp = reportData as unknown as Parameters<typeof adaptReport>[0]
    const blocks = adaptReport(resp)
    const entry: ReportEntry = {
      id: uuid(),
      query: queryText || '历史报告',
      content: ((reportData.answer as Record<string, unknown>)?.text as string) || '',
      blocks,
      steps: [],
      timestamp: Date.now(),
      status: 'done',
    }
    useSessionStore.setState({ currentReport: entry, viewMode: 'report' })
  }, [])

  function getReportFromMessage(msg: ConversationMessage): Record<string, unknown> | null {
    if (msg.message_type === 'report' && msg.content) {
      try { return JSON.parse(msg.content) } catch { return null }
    }
    return null
  }

  type DisplayMsg = {
    id: string
    role: 'user' | 'assistant'
    content: string
    msgType?: string
    isStreaming?: boolean
    reportData?: Record<string, unknown> | null
  }

  const displayMessages: DisplayMsg[] = []

  for (const msg of chatMessages) {
    const reportData = getReportFromMessage(msg)
    displayMessages.push({
      id: `hist-${msg.id}`,
      role: msg.role,
      content: reportData
        ? ((reportData.answer as Record<string, unknown>)?.text as string) || '报告已生成'
        : (msg.content || ''),
      msgType: msg.message_type,
      reportData,
    })
  }

  if (currentReport || isStreaming) {
    if (currentReport?.query) {
      displayMessages.push({
        id: 'cur-user',
        role: 'user',
        content: currentReport.query,
      })
    }

    const runningNode = timeline.find(t => t.status === 'running')
    const statusText = !runningNode ? '正在分析数据...'
      : runningNode.nodeName.includes('sql') || runningNode.nodeName.includes('plan') ? '正在生成查询...'
      : runningNode.nodeName.includes('execute') ? '正在执行查询...'
      : runningNode.nodeName.includes('report') || runningNode.nodeName.includes('chart') || runningNode.nodeName.includes('可视化') ? '正在生成报表...'
      : runningNode.nodeName.includes('security') ? '正在安全审查...'
      : '正在分析数据...'

    displayMessages.push({
      id: 'cur-ast',
      role: 'assistant',
      content: isStreaming ? statusText : (currentReport?.content || ''),
      isStreaming,
      msgType: isStreaming ? 'streaming' : 'done',
    })
  }

  const hasMessages = displayMessages.length > 0

  if (!hasMessages) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <EmptyState onExampleClick={handleExample} />
        </div>
        <InputArea value={inputText} onChange={setInputText} onSend={handleSend} disabled={isStreaming} />
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        flex: 1, overflowY: 'auto', padding: '20px 24px',
        display: 'flex', flexDirection: 'column', gap: 16,
      }}>
        {displayMessages.map((msg) =>
          msg.role === 'user' ? (
            <div
              key={msg.id}
              style={{
                display: 'flex', flexDirection: 'row-reverse', gap: 8,
                maxWidth: '85%', alignSelf: 'flex-end',
              }}
            >
              <div style={{
                width: 28, height: 28, borderRadius: 14,
                background: 'linear-gradient(135deg, #1E40AF, #3B82F6)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, color: '#fff', fontSize: 13,
                boxShadow: '0 2px 4px rgba(30,64,175,0.3)',
              }}>
                <UserOutlined />
              </div>
              <div style={{
                ...styles.messageBubble,
                background: 'linear-gradient(135deg, #1E40AF, #2563EB)',
                color: '#FFFFFF',
                borderBottomRightRadius: 2,
              }}>
                {msg.content}
              </div>
            </div>
          ) : (
            <div
              key={msg.id}
              style={{
                display: 'flex', gap: 8,
                maxWidth: '85%', alignSelf: 'flex-start',
              }}
            >
              <div style={{
                width: 28, height: 28, borderRadius: 14,
                background: '#F1F5F9',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, color: '#64748B', fontSize: 13,
                border: '1px solid #E2E8F0',
              }}>
                <RobotOutlined />
              </div>
              <div style={{
                ...styles.messageBubble,
                background: '#FFFFFF',
                border: '1px solid #E2E8F0',
                borderBottomLeftRadius: 2,
                color: '#1E293B',
                minWidth: 60,
              }}>
                {msg.isStreaming ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Spin indicator={<LoadingOutlined style={{ fontSize: 14, color: '#3B82F6' }} />} size="small" />
                    <Text style={{ color: '#64748B', fontSize: 12 }}>{msg.content}</Text>
                  </div>
                ) : msg.reportData ? (
                  <div>
                    <Text style={{ fontSize: 12, color: '#1E293B' }}>{msg.content}</Text>
                    <div style={{ marginTop: 10 }}>
                      <Button
                        type="primary"
                        size="small"
                        ghost
                        onClick={() => viewReport(msg.reportData!, msg.content)}
                        style={{ fontSize: 12, padding: '2px 12px', borderRadius: 6 }}
                      >
                        <FileTextOutlined style={{ marginRight: 4 }} />查看完整报告 →
                      </Button>
                    </div>
                  </div>
                ) : msg.msgType === 'error' ? (
                  <Text style={{ color: '#DC2626', fontSize: 12 }}>{msg.content}</Text>
                ) : msg.msgType === 'clarify' ? (
                  <Text style={{ fontSize: 12, color: '#1E40AF' }}>{msg.content}</Text>
                ) : !msg.content ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <CheckCircleOutlined style={{ color: '#059669', fontSize: 14 }} />
                    <Text style={{ fontSize: 12, color: '#059669' }}>报告生成完成</Text>
                    <Button
                      type="primary"
                      size="small"
                      ghost
                      onClick={() => setViewMode('report')}
                      style={{ fontSize: 12, padding: '2px 12px', borderRadius: 6, marginLeft: 4 }}
                    >
                      查看完整报告 →
                    </Button>
                  </div>
                ) : (
                  <Text style={{ fontSize: 13, whiteSpace: 'pre-wrap', color: '#1E293B' }}>{msg.content}</Text>
                )}
              </div>
            </div>
          )
        )}
        {error && !isStreaming && (
          <div style={{
            background: '#FEF2F2',
            border: '1px solid #FECACA',
            borderRadius: 8,
            padding: '10px 14px',
            fontSize: 12,
            color: '#DC2626',
          }}>
            {error}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <InputArea value={inputText} onChange={setInputText} onSend={handleSend} disabled={isStreaming} />
    </div>
  )
}

function InputArea({ value, onChange, onSend, disabled }: {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  disabled: boolean
}) {
  const [focused, setFocused] = useState(false)

  return (
    <div style={{
      padding: '12px 16px',
      borderTop: '1px solid #E2E8F0',
      background: '#FFFFFF',
      flexShrink: 0,
    }}>
      <div style={{
        display: 'flex',
        gap: 8,
        alignItems: 'flex-end',
        background: '#F8FAFC',
        border: `1px solid ${focused ? '#3B82F6' : '#E2E8F0'}`,
        borderRadius: 10,
        padding: '6px 6px 6px 14px',
        transition: 'border-color 0.2s',
      }}>
        <TextArea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
          placeholder="输入分析问题，例如：2024年各区域销售总额"
          autoSize={{ minRows: 1, maxRows: 4 }}
          disabled={disabled}
          variant="borderless"
          style={{ fontSize: 13, resize: 'none' }}
        />
        <Button
          type="primary"
          shape="round"
          icon={<SendOutlined />}
          onClick={onSend}
          disabled={disabled || !value.trim()}
          loading={disabled}
          style={{
            height: 34,
            minWidth: 34,
            width: disabled ? 'auto' : 34,
            padding: disabled ? '0 14px' : 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            boxShadow: disabled ? 'none' : '0 2px 4px rgba(30,64,175,0.3)',
          }}
        >
          {disabled ? '生成中' : ''}
        </Button>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6, padding: '0 4px' }}>
        <Text style={{ fontSize: 11, color: '#94A3B8' }}>Shift+Enter 换行 · Enter 发送</Text>
      </div>
    </div>
  )
}