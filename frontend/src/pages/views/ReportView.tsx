import { IconArrowLeft, IconClock, IconFile, IconLoading } from '../../components/ui/Icons'
import { useSessionStore } from '../../stores/session'
import ReportRenderer from '../../components/report/ReportRenderer'
import { Text } from '../../components/atelier/Typography'
import Button from '../../components/atelier/Button'
import Card from '../../components/atelier/Card'

export default function ReportView() {
  const { currentReport, busy, timeline, setViewMode } = useSessionStore()

  if (!currentReport) return null

  const hasBlocks = currentReport.blocks.length > 0
  const hasTimelineEvents = timeline.length > 0

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--canvas)' }}>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', width: '100%', padding: '28px 32px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

            <Card
              bodyStyle={{ padding: '24px 28px' }}
              style={{ borderRadius: 12 }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
                    <Button
                      variant="quiet"
                      onClick={() => setViewMode('chat')}
                      size="sm"
                      style={{ color: 'var(--muted)', fontSize: 12, marginLeft: -6 }}
                    >
                      <IconArrowLeft /> 返回对话
                    </Button>
                  </div>
                  <h1 style={{
                    fontSize: 22,
                    fontWeight: 700,
                    color: 'var(--ink)',
                    margin: 0,
                    letterSpacing: '-0.3px',
                  }}>
                    数据分析报告
                  </h1>
                  <div style={{ marginTop: 8, display: 'flex', gap: 20, color: 'var(--muted)', fontSize: 12 }}>
                    <span>
                      <IconClock style={{ marginRight: 6, color: 'var(--faint)' }} />
                      {new Date(currentReport.timestamp).toLocaleString('zh-CN')}
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <IconFile style={{ color: 'var(--faint)' }} />
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
                    background: 'var(--teal-soft)',
                    color: 'var(--teal-deep)',
                    padding: '4px 14px',
                    borderRadius: 20,
                    fontSize: 12,
                    fontWeight: 500,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    border: '1px solid var(--teal-pale)',
                  }}>
                    <IconLoading style={{ width: 12, height: 12 }} />
                    生成中...
                  </div>
                )}
              </div>
            </Card>

            {hasBlocks ? (
              <div style={{ animation: 'fadeInUp 0.3s ease' }}>
                <ReportRenderer blocks={currentReport.blocks} />
              </div>
            ) : busy ? (
              <Card style={{ borderRadius: 12 }}>
                <div style={{ textAlign: 'center', padding: '40px 24px' }}>
                  <IconLoading style={{ width: 36, height: 36, color: 'var(--teal)' }} />
                  <Text style={{ display: 'block', fontSize: 15, color: 'var(--ink-2)', marginTop: 16, fontWeight: 500 }}>
                    正在执行数据分析...
                  </Text>
                  <Text style={{ display: 'block', fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
                    Agent 正在处理你的查询，请稍候
                  </Text>
                  {hasTimelineEvents && (
                    <div style={{ marginTop: 24 }}>
                      {timeline.map((t) => (
                        <div key={t.id} style={{ padding: '6px 0', borderBottom: '1px solid var(--line)', fontSize: 12, color: 'var(--muted)' }}>
                          {t.nodeName}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </Card>
            ) : (
              <Card style={{ borderRadius: 12 }}>
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Text style={{ color: 'var(--muted)', fontSize: 13 }}>
                    查询完成，但未生成报表内容
                  </Text>
                </div>
              </Card>
            )}

            {!busy && hasBlocks && (
              <div style={{ textAlign: 'center', paddingTop: 8, paddingBottom: 4 }}>
                <Text style={{ fontSize: 11, color: 'var(--faint)' }}>
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
