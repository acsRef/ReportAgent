import { Typography, Button, Card, Skeleton } from 'antd'
import { ArrowLeftOutlined, ClockCircleOutlined, FileTextOutlined, LoadingOutlined } from '@ant-design/icons'
import { useSessionStore } from '../../stores/session'
import ReportRenderer from '../../components/report/ReportRenderer'

const { Text } = Typography

export default function ReportView() {
  const { currentReport, busy, timeline, setViewMode } = useSessionStore()

  if (!currentReport) return null

  const hasBlocks = currentReport.blocks.length > 0
  const hasTimelineEvents = timeline.length > 0

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#F8FAFC' }}>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', width: '100%', padding: '28px 32px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

            {/* Report Header */}
            <Card
              styles={{ body: { padding: '24px 28px' } }}
              style={{
                borderRadius: 12,
                boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
                    <Button
                      type="text"
                      icon={<ArrowLeftOutlined />}
                      onClick={() => setViewMode('chat')}
                      size="small"
                      style={{ color: '#64748B', fontSize: 12, marginLeft: -6 }}
                    >
                      返回对话
                    </Button>
                  </div>
                  <h1 style={{
                    fontSize: 22,
                    fontWeight: 700,
                    color: '#1E293B',
                    margin: 0,
                    letterSpacing: '-0.3px',
                  }}>
                    数据分析报告
                  </h1>
                  <div style={{ marginTop: 8, display: 'flex', gap: 20, color: '#64748B', fontSize: 12 }}>
                    <span>
                      <ClockCircleOutlined style={{ marginRight: 6, color: '#94A3B8' }} />
                      {new Date(currentReport.timestamp).toLocaleString('zh-CN')}
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <FileTextOutlined style={{ color: '#94A3B8' }} />
                      <span style={{
                        maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap', display: 'inline-block',
                      }}>
                        {currentReport.query}
                      </span>
                    </span>
                  </div>
                </div>
                {busy && (
                  <div style={{
                    background: '#EFF6FF',
                    color: '#1E40AF',
                    padding: '4px 14px',
                    borderRadius: 20,
                    fontSize: 12,
                    fontWeight: 500,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    border: '1px solid #DBEAFE',
                  }}>
                    <LoadingOutlined style={{ fontSize: 12 }} />
                    生成中...
                  </div>
                )}
              </div>
            </Card>

            {/* Report Content */}
            {hasBlocks ? (
              <div style={{ animation: 'fadeInUp 0.3s ease' }}>
                <ReportRenderer blocks={currentReport.blocks} />
              </div>
            ) : busy ? (
              <Card
                style={{
                  borderRadius: 12,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
                }}
              >
                <div style={{ textAlign: 'center', padding: '40px 24px' }}>
                  <LoadingOutlined style={{ fontSize: 36, color: '#3B82F6' }} />
                  <Text style={{ display: 'block', fontSize: 15, color: '#475569', marginTop: 16, fontWeight: 500 }}>
                    正在执行数据分析...
                  </Text>
                  <Text style={{ display: 'block', fontSize: 12, color: '#94A3B8', marginTop: 4 }}>
                    Agent 正在处理你的查询，请稍候
                  </Text>
                  {hasTimelineEvents && <Skeleton active paragraph={{ rows: 3 }} style={{ marginTop: 24 }} />}
                </div>
              </Card>
            ) : (
              <Card
                style={{
                  borderRadius: 12,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
                }}
              >
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Text style={{ color: '#94A3B8', fontSize: 13 }}>
                    查询完成，但未生成报表内容
                  </Text>
                </div>
              </Card>
            )}

            {/* Footer */}
            {!busy && hasBlocks && (
              <div style={{ textAlign: 'center', paddingTop: 8, paddingBottom: 4 }}>
                <Text style={{ fontSize: 11, color: '#CBD5E1' }}>
                  — ReportAgent AI 自动生成 —
                </Text>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}