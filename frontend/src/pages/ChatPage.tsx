import { useCallback, useRef, useEffect, useState } from 'react'
import { Typography, Select, Button, Input, Skeleton, Spin } from 'antd'
import {
  SendOutlined, ReloadOutlined, RobotOutlined, UserOutlined,
  FileTextOutlined, ClockCircleOutlined, LoadingOutlined, CheckCircleOutlined,
} from '@ant-design/icons'
import { useSessionStore } from '../stores/session'
import AgentTimeline from '../components/chat/AgentTimeline'
import ReportRenderer from '../components/report/ReportRenderer'
import EmptyState from '../components/chat/EmptyState'

const { Text } = Typography
const { TextArea } = Input

export default function ChatPage() {
  const {
    currentReport, isStreaming, timeline,
    sessionLabel, templateParams, error,
    sendMessage, setTemplateParams,
  } = useSessionStore()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [inputText, setInputText] = useState('')

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [currentReport?.content, currentReport?.steps])

  const handleSend = useCallback(() => {
    const trimmed = inputText.trim()
    if (!trimmed || isStreaming) return
    sendMessage(trimmed)
    setInputText('')
  }, [inputText, isStreaming, sendMessage])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleExample = useCallback((query: string) => {
    sendMessage(query)
  }, [sendMessage])

  function cleanContent(raw: string): string {
    return raw
      .replace(/<think>[\s\S]*?<\/think>/g, '')
      .replace(/```[\s\S]*?```/g, '')
      .replace(/^ping - .*/gm, '')
      // .replace(/^[{\[].*[}\]]$/gm, '') — removed: too aggressive, kills JSON-like insight text
      .replace(/\n{3,}/g, '\n\n')
      .trim()
  }

  function isNaturalLanguage(text: string): boolean {
    const cn = (text.match(/[\u4e00-\u9fff]/g) || []).length
    return cn > 10
  }

  const hasReport = !!currentReport
  const hasBlocks = currentReport && currentReport.blocks.length > 0
  const hasTimelineEvents = timeline.length > 0
  const showEmptyInitial = !hasReport && !isStreaming
  const reportContent = currentReport?.content ? cleanContent(currentReport.content) : ''
  const hasMeaningfulContent = reportContent.length > 0 && reportContent !== '查询完成' && isNaturalLanguage(reportContent)

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left Panel */}
      <aside
        style={{
          width: 360,
          background: '#fff',
          borderRight: '1px solid #e8e8e8',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
        }}
      >
        {/* Chat Workspace */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
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
              💬 Chat Workspace
            </Text>
            <Text style={{ fontSize: 11, color: '#999' }}>
              Session: {sessionLabel}
            </Text>
          </div>

          <div style={{
            flex: 1,
            overflowY: 'auto',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}>
            {showEmptyInitial ? (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <EmptyState onExampleClick={handleExample} />
              </div>
            ) : (
              <>
                {currentReport && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {/* User message */}
                    <div style={{ display: 'flex', flexDirection: 'row-reverse', gap: 8, maxWidth: '88%', alignSelf: 'flex-end' }}>
                      <div style={{
                        width: 28, height: 28, borderRadius: 14,
                        background: '#1677ff', display: 'flex', alignItems: 'center',
                        justifyContent: 'center', flexShrink: 0, color: '#fff', fontSize: 13,
                      }}>
                        <UserOutlined />
                      </div>
                      <div style={{
                        background: '#1677ff', color: '#fff',
                        padding: '8px 12px', borderRadius: 8, borderBottomRightRadius: 2,
                        fontSize: 13, lineHeight: 1.5,
                      }}>
                        {currentReport.query}
                      </div>
                    </div>

                    {/* Agent response */}
                    <div style={{ display: 'flex', gap: 8, maxWidth: '88%', alignSelf: 'flex-start' }}>
                      <div style={{
                        width: 28, height: 28, borderRadius: 14,
                        background: '#f0f0f0', display: 'flex', alignItems: 'center',
                        justifyContent: 'center', flexShrink: 0, color: '#666', fontSize: 13,
                      }}>
                        <RobotOutlined />
                      </div>
                      <div style={{
                        background: '#f5f6f9', border: '1px solid #e8e8e8',
                        padding: '8px 12px', borderRadius: 8, borderBottomLeftRadius: 2,
                        fontSize: 13, lineHeight: 1.5, color: '#333',
                      }}>
                        {isStreaming ? (
                          <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} />} size="small" />
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {(() => {
                                  const running = timeline.find(t => t.status === 'running')
                                  if (!running) return '正在分析数据...'
                                  const n = running.nodeName
                                  if (n.includes('search') || n.includes('数据')) return '正在搜索数据表...'
                                  if (n.includes('sql') || n.includes('SQL') || n.includes('plan') || n.includes('规划')) return '正在生成查询...'
                                  if (n.includes('execute') || n.includes('执行')) return '正在执行查询...'
                                  if (n.includes('report') || n.includes('报表') || n.includes('chart') || n.includes('可视化')) return '正在生成报表...'
                                  if (n.includes('classify') || n.includes('分类')) return '正在理解问题...'
                                  if (n.includes('security') || n.includes('安全')) return '正在安全审查...'
                                  if (n.includes('clarify') || n.includes('追问')) return '正在分析...'
                                  if (n.includes('evaluate') || n.includes('评估')) return '正在评估结果...'
                                  return '正在分析数据...'
                                })()}
                              </Text>
                            </div>
                          </div>
                        ) : error ? (
                          <Text style={{ color: '#cf1322', fontSize: 12 }}>{error}</Text>
                        ) : hasMeaningfulContent ? (
                          <Text style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
                            {reportContent}
                          </Text>
                        ) : hasBlocks ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 14 }} />
                            <Text style={{ fontSize: 12, color: '#52c41a' }}>报告生成完成</Text>
                          </div>
                        ) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <CheckCircleOutlined style={{ color: '#8f959e', fontSize: 14 }} />
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              查询完成，请查看右侧报告
                            </Text>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {error && isStreaming === false && (
                  <div style={{
                    background: '#fff2f0', border: '1px solid #ffccc7',
                    borderRadius: 6, padding: '8px 12px', fontSize: 12, color: '#cf1322',
                  }}>
                    {error}
                  </div>
                )}

                <div ref={messagesEndRef} />
              </>
            )}
          </div>

          {/* Input area */}
          <div style={{
            padding: '10px 12px',
            borderTop: '1px solid #f0f0f0',
            background: '#fff',
            flexShrink: 0,
          }}>
            <TextArea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入分析问题，例如：2024年各区域销售总额"
              autoSize={{ minRows: 2, maxRows: 4 }}
              disabled={isStreaming}
              style={{ fontSize: 13, borderRadius: 6 }}
            />
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              alignItems: 'center', marginTop: 6,
            }}>
              <Text style={{ fontSize: 11, color: '#bbb' }}>Shift+Enter 换行</Text>
              <Button
                type="primary"
                size="small"
                icon={<SendOutlined />}
                onClick={handleSend}
                disabled={isStreaming || !inputText.trim()}
                loading={isStreaming}
                style={{ fontSize: 12 }}
              >
                {isStreaming ? '生成中' : '发送'}
              </Button>
            </div>
          </div>
        </div>

        {/* Agent Timeline */}
        <AgentTimeline events={timeline} isStreaming={isStreaming} />
      </aside>

      {/* Right Panel */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#f5f6f9' }}>
        {/* Template Param Bar */}
        <div style={{
          background: '#fff',
          padding: '10px 24px',
          borderBottom: '1px solid #e8e8e8',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
          boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <Text strong style={{ fontSize: 13, color: '#555' }}>
              {hasReport ? '当前报告' : '对话画布'}
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 12, color: '#888' }}>年份:</span>
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
              <span style={{ fontSize: 12, color: '#888' }}>区域:</span>
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
          </div>
          <Button size="small" icon={<ReloadOutlined />} style={{ fontSize: 12 }} disabled>
            刷新参数并重新生成
          </Button>
        </div>

        {/* Report Content */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div style={{ maxWidth: 1100, margin: '0 auto', width: '100%', padding: '24px 32px' }}>
            {currentReport ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                {/* Report Header */}
                <div style={{
                  background: '#fff',
                  padding: '20px 24px',
                  borderRadius: 8,
                  border: '1px solid #e8e8e8',
                  boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}>
                  <div>
                    <h1 style={{ fontSize: 20, fontWeight: 600, color: '#1f2329', margin: 0 }}>
                      数据分析报告
                    </h1>
                    <div style={{ marginTop: 6, display: 'flex', gap: 16, color: '#8f959e', fontSize: 12 }}>
                      <span>
                        <ClockCircleOutlined style={{ marginRight: 4 }} />
                        {new Date(currentReport.timestamp).toLocaleString('zh-CN')}
                      </span>
                      <span>
                        <FileTextOutlined style={{ marginRight: 4 }} />
                        {currentReport.query}
                      </span>
                    </div>
                  </div>
                  {isStreaming && (
                    <div style={{
                      background: '#e6f4ff', color: '#1677ff',
                      padding: '4px 12px', borderRadius: 12, fontSize: 12, fontWeight: 500,
                      display: 'flex', alignItems: 'center', gap: 4,
                    }}>
                      <LoadingOutlined style={{ fontSize: 12 }} />
                      生成中...
                    </div>
                  )}
                </div>

                {/* Report content */}
                {hasBlocks ? (
                  <ReportRenderer blocks={currentReport.blocks} />
                ) : isStreaming ? (
                  <div style={{
                    background: '#fff', border: '1px solid #e8e8e8',
                    borderRadius: 8, padding: 32,
                    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                    textAlign: 'center',
                  }}>
                    <div style={{ marginBottom: 16 }}>
                      <LoadingOutlined style={{ fontSize: 32, color: '#1677ff' }} />
                    </div>
                    <Text style={{ display: 'block', fontSize: 14, color: '#555', marginBottom: 8 }}>
                      正在执行数据分析...
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      Agent 正在调取数据、生成报告，请稍候
                    </Text>
                    {hasTimelineEvents && (
                      <div style={{ marginTop: 16 }}>
                        <Skeleton active paragraph={{ rows: 3 }} />
                      </div>
                    )}
                  </div>
                  ) : hasMeaningfulContent ? (
                  <div style={{
                    background: '#fff', border: '1px solid #e8e8e8',
                    borderRadius: 8, padding: '20px 24px',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                  }}>
                    <Text style={{ fontSize: 14, whiteSpace: 'pre-wrap', color: '#333', lineHeight: 1.8 }}>
                      {reportContent}
                    </Text>
                  </div>
                ) : error ? (
                  <div style={{
                    background: '#fff2f0', border: '1px solid #ffccc7',
                    borderRadius: 8, padding: 40, textAlign: 'center',
                  }}>
                    <Text style={{ color: '#cf1322', fontSize: 14, display: 'block', marginBottom: 8 }}>
                      请求失败
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {error}
                    </Text>
                  </div>
                ) : (
                  <div style={{
                    background: '#fff', border: '1px solid #e8e8e8',
                    borderRadius: 8, padding: 40, textAlign: 'center',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                  }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      查询完成，但未生成报表内容
                    </Text>
                  </div>
                )}

                {!isStreaming && hasBlocks && (
                  <div style={{ textAlign: 'center', paddingTop: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      — ReportAgent AI 自动生成 —
                    </Text>
                  </div>
                )}
              </div>
            ) : (
              <EmptyState onExampleClick={handleExample} />
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
