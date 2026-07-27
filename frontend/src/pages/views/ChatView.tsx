import { useState, useCallback, useRef, useEffect } from 'react'
import { v4 as uuid } from 'uuid'

import { Text } from '../../components/atelier/Typography'
import Button from '../../components/atelier/Button'
import TextArea from '../../components/atelier/TextArea'
import Spinner from '../../components/atelier/Spinner'
import { useSessionStore } from '../../stores/session'
import { adaptReport } from '../../adapter/reportAdapter'
import EmptyState from '../../components/chat/EmptyState'
import ChatCards from '../../components/chat/ChatCards'
import { IconCheckCircle, IconChevronRight, IconLogo, IconReport, IconSend, IconStop, IconUser } from '../../components/ui/Icons'
import type { ConversationMessage, ReportEntry, ChatCard } from '../../types/report'
import { isChatCard } from '../../types/report'

const styles: Record<string, React.CSSProperties> = {
  messageBubble: {
    padding: '12px 16px',
    borderRadius: 8,
    fontSize: 16,
    lineHeight: 1.5,
    boxShadow: 'none',
  },
}

export default function ChatView() {
  const {
    currentReport, busy, timeline, error, chatMessages, cardsByMessage,
    intentPhase, busyStartedAt,
    sendMessage, setViewMode, cancel,
  } = useSessionStore()

  const [inputText, setInputText] = useState('')
  const [now, setNow] = useState(Date.now())
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Heartbeat for the busy-timeout / "is it stuck?" banner.
  useEffect(() => {
    if (!busy) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [busy])

  // 60s auto-cancel: if the busy state has lasted 60s and the user hasn't
  // been nudged, give up and turn this into an error.
  useEffect(() => {
    if (!busy || !busyStartedAt) return
    const elapsed = now - busyStartedAt
    const remaining = 60_000 - elapsed
    if (remaining <= 0) {
      cancel()
      useSessionStore.setState({
        error: '生成超过 60 秒未响应,已自动取消',
        intentPhase: 'error',
      })
      return
    }
    if (remaining > 5000) return
    const t = setTimeout(() => {
      cancel()
      useSessionStore.setState({
        error: '生成超过 60 秒未响应,已自动取消',
        intentPhase: 'error',
      })
    }, remaining)
    return () => clearTimeout(t)
  }, [busy, busyStartedAt, now, cancel])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [currentReport?.content, currentReport?.steps, chatMessages])

  const handleSend = useCallback(() => {
    const trimmed = inputText.trim()
    if (!trimmed || busy) return
    sendMessage(trimmed)
    setInputText('')
  }, [inputText, busy, sendMessage])

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
    busy?: boolean
    reportData?: Record<string, unknown> | null
    cards?: ChatCard[]
    showStuck?: boolean
    stuckSeconds?: number
  }

  const displayMessages: DisplayMsg[] = []

  for (const msg of chatMessages) {
    const reportData = getReportFromMessage(msg)
    const cards = msg.metadata?.cards?.filter(isChatCard)
    displayMessages.push({
      id: `hist-${msg.id}`,
      role: msg.role,
      content: reportData
        ? ((reportData.answer as Record<string, unknown>)?.text as string) || '报告已生成'
        : (msg.content || ''),
      msgType: msg.message_type,
      reportData,
      cards,
    })
  }

  if (currentReport || busy || intentPhase !== 'idle') {
    if (currentReport?.query) {
      displayMessages.push({
        id: 'cur-user',
        role: 'user',
        content: currentReport.query,
      })
    }

    const runningNode = timeline.length > 0 ? timeline[timeline.length - 1] : null
    const phaseText =
      intentPhase === 'analyzing' && busy ? '🧠 正在分析你的意图…'
      : intentPhase === 'clarifying' && busy ? '💭 等待你确认查询维度…'
      : intentPhase === 'error' ? '⚠️ 生成已中断'
      : !runningNode ? '正在分析数据…'
      : runningNode.nodeName.match(/plan_clarify|intent_analyz/) ? '🧠 正在分析你的意图…'
      : runningNode.nodeName.match(/sql_plan|_plan[^_]|^plan$/) ? '📋 正在规划查询…'
      : runningNode.nodeName.match(/sql_generate|generate_sql/) ? '✍️ 正在生成 SQL…'
      : runningNode.nodeName.match(/sql_validate|sql_evaluat/) ? '🔍 正在校验 SQL…'
      : runningNode.nodeName.match(/sql_execute|^execute$/) ? '⏳ 正在执行查询…'
      : runningNode.nodeName.match(/build_output/) ? '🧮 正在整理结果…'
      : runningNode.nodeName.match(/plan_analysis/) ? '📊 正在分析数据模式…'
      : runningNode.nodeName.match(/run_step/) ? '📈 正在生成可视化…'
      : runningNode.nodeName.match(/report_build/) ? '🎨 正在装配最终报告…'
      : runningNode.nodeName.match(/security_guard/) ? '🛡️ 正在安全审查…'
      : '正在分析数据…'

    const statusText = busy
      ? phaseText
      : (intentPhase === 'report' ? '' : (currentReport?.content || ''))

    // Stuck banner: show after 30s of silence.
    const stuckSeconds = (busy && busyStartedAt)
      ? Math.floor((now - busyStartedAt) / 1000)
      : 0
    const showStuck = busy && stuckSeconds >= 30

    displayMessages.push({
      id: currentReport?.id ?? 'cur-ast',
      role: 'assistant',
      content: statusText,
      busy,
      showStuck,
      stuckSeconds,
      msgType: busy ? 'streaming' : (intentPhase === 'error' ? 'error' : 'done'),
      cards: currentReport ? cardsByMessage[currentReport.id] : undefined,
    })
  }

  const hasMessages = displayMessages.length > 0

  if (!hasMessages) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <EmptyState onExampleClick={handleExample} />
        </div>
        <InputArea inputRef={inputRef} value={inputText} onChange={setInputText} onSend={handleSend} onCancel={cancel} disabled={busy} />
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        flex: 1, overflowY: 'auto', padding: 24,
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
                width: 32, height: 32, borderRadius: 16,
                background: '#EFF6FF',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, color: '#1677FF', fontSize: 14,
                border: '1px solid #E5E7EB',
              }}>
                <IconUser />
              </div>
              <div style={{
                ...styles.messageBubble,
                background: '#EFF6FF',
                color: '#0F172A',
                border: '1px solid #E5E7EB',
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
                width: 32, height: 32, borderRadius: 16,
                background: '#FAFAFA',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, color: '#6B7280', fontSize: 14,
                border: '1px solid #E5E7EB',
              }}>
                <IconLogo />
              </div>
              <div style={{
                ...styles.messageBubble,
                background: '#FFFFFF',
                border: '1px solid #E5E7EB',
                color: '#0F172A',
                minWidth: 60,
              }}>
                {msg.busy ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Spinner size="sm" />
                    <Text style={{ color: 'var(--muted)', fontSize: 12 }}>{msg.content}</Text>
                  </div>
                ) : msg.reportData ? (
                  <div>
                    <Text style={{ fontSize: 14 }}>{msg.content}</Text>
                    <div style={{ marginTop: 12 }}>
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => viewReport(msg.reportData!, msg.content)}
                        style={{ fontSize: 13 }}
                      >
                        <IconReport /> 查看完整报告 <IconChevronRight />
                      </Button>
                    </div>
                  </div>
                ) : msg.msgType === 'error' ? (
                  <Text type="danger" style={{ fontSize: 12 }}>{msg.content}</Text>
                ) : msg.msgType === 'clarify' ? (
                  <Text style={{ fontSize: 14, color: 'var(--teal)' }}>{msg.content}</Text>
                ) : !msg.content && !msg.cards?.length ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <IconCheckCircle style={{ color: 'var(--green)', width: 14, height: 14 }} />
                    <Text style={{ fontSize: 12, color: 'var(--green)' }}>报告生成完成</Text>
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => setViewMode('report')}
                      style={{ fontSize: 13, marginLeft: 4 }}
                    >
                      查看完整报告 <IconChevronRight />
                    </Button>
                  </div>
                ) : (
                  <div>
                    {msg.content ? <Text style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>{msg.content}</Text> : null}
                    {msg.cards?.map((card) => (
                      <ChatCards
                        key={`${card.id}-${card.version}`}
                        type={card.type}
                        data={card}
                        cardId={card.id}
                        onModify={() => {
                          const synthesized = 'synthesized_query' in card ? card.synthesized_query : undefined
                          setInputText(synthesized ?? currentReport?.query ?? '')
                          window.setTimeout(() => inputRef.current?.focus(), 0)
                        }}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        )}
        {displayMessages.some((m) => m.showStuck) && (
          <div style={{
            alignSelf: 'center',
            background: '#FEF3C7', border: '1px solid #F59E0B',
            borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#92400E',
            display: 'flex', alignItems: 'center', gap: 10, maxWidth: '70%',
          }}>
            <span>⏳ 这次想得有点久(已等 {(displayMessages.find((m) => m.showStuck)?.stuckSeconds ?? 0)}s)。要不要继续?</span>
            <Button variant="default" size="sm" onClick={() => { useSessionStore.getState().cancel() }}
              style={{ fontSize: 12 }}>
              重试
            </Button>
            <Button variant="danger" size="sm" onClick={() => { useSessionStore.getState().cancel(); setInputText(currentReport?.query ?? '') }}
              style={{ fontSize: 12 }}>
              取消
            </Button>
          </div>
        )}

        {error && !busy && (
          <div style={{
            alignSelf: 'center',
            background: '#FEF2F2',
            border: '1px solid #EF4444',
            borderRadius: 8,
            padding: '12px 16px',
            fontSize: 14,
            color: '#991B1B',
            maxWidth: '70%',
            display: 'flex', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 16 }}>⚠️</span>
              <strong>生成已中断</strong>
            </div>
            <span style={{ fontSize: 13 }}>{error}</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="primary" size="sm" onClick={() => {
                if (currentReport?.query) sendMessage(currentReport.query)
              }}
                style={{ fontSize: 12 }}>
                重试同样条件
              </Button>
              <Button variant="default" size="sm" onClick={() => {
                setInputText(currentReport?.query ?? '')
                window.setTimeout(() => inputRef.current?.focus(), 0)
              }}
                style={{ fontSize: 12 }}>
                修改问题
              </Button>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <InputArea inputRef={inputRef} value={inputText} onChange={setInputText} onSend={handleSend} onCancel={cancel} disabled={busy} />
    </div>
  )
}

interface InputAreaProps {
  inputRef: React.RefObject<HTMLTextAreaElement | null>
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onCancel: () => void
  disabled: boolean
}

function InputArea({ inputRef, value, onChange, onSend, onCancel, disabled }: InputAreaProps) {
  const [focused, setFocused] = useState(false)

  return (
    <div style={{
      padding: '12px 24px',
      borderTop: '1px solid var(--line)',
      background: 'var(--paper)',
      flexShrink: 0,
    }}>
      <div style={{
        display: 'flex',
        gap: 8,
        alignItems: 'flex-end',
        background: 'var(--paper)',
        border: `1px solid ${focused ? 'var(--teal)' : 'var(--line)'}`,
        borderRadius: 8,
        padding: '8px 8px 8px 16px',
        transition: 'border-color 0.2s',
      }}>
        <TextArea
          ref={inputRef}
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
          disabled={disabled}
          style={{ fontSize: 14, lineHeight: 1.5, resize: 'none', color: 'var(--ink)', flex: 1, border: 'none', outline: 'none', background: 'transparent' }}
        />
        <Button
          variant="primary"
          onClick={disabled ? onCancel : onSend}
          disabled={!disabled && !value.trim()}
          style={{
            height: 32,
            minWidth: 32,
            width: disabled ? 'auto' : 32,
            padding: disabled ? '0 12px' : 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            fontWeight: 500,
            fontSize: 14,
          }}
        >
          {disabled ? <><IconStop /> 停止</> : <IconSend />}
        </Button>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6, padding: '0 4px' }}>
        <Text style={{ fontSize: 11, color: 'var(--muted)' }}>Shift+Enter 换行 · Enter 发送</Text>
      </div>
    </div>
  )
}