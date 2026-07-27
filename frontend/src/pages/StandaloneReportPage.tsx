import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Text, Title } from '../components/atelier/Typography'
import ReportRenderer from '../components/report/ReportRenderer'
import { useSessionStore } from '../stores/session'
import type { ReportBlock } from '../types/report'

function isReportBlock(value: unknown): value is ReportBlock {
  return (
    typeof value === 'object' && value !== null &&
    'id' in value && typeof value.id === 'string' &&
    'type' in value && typeof value.type === 'string' &&
    'data' in value && typeof value.data === 'object' && value.data !== null
  )
}

export default function StandaloneReportPage() {
  const { sessionId } = useParams()
  const [searchParams] = useSearchParams()
  const pendingReportBlocks = useSessionStore((state) => state.pendingReportBlocks)
  let queryBlocks: ReportBlock[] | null = null

  try {
    const parsed: unknown = JSON.parse(searchParams.get('blocks') ?? 'null')
    if (Array.isArray(parsed) && parsed.every(isReportBlock)) queryBlocks = parsed
  } catch { /* malformed report URL */ }

  const blocks = pendingReportBlocks ?? queryBlocks

  return (
    <div style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
      <header style={{ height: 56, padding: '0 24px', background: 'var(--paper)', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Link to="/" style={{ color: 'var(--teal)', fontSize: 14 }}>← 返回对话</Link>
        <Text type="secondary" style={{ fontSize: 12 }}>Session: {sessionId}</Text>
      </header>
      <main style={{ maxWidth: 1100, margin: '0 auto', padding: '28px 32px' }}>
        <Title level={2} style={{ marginTop: 0 }}>数据分析报告</Title>
        {blocks ? (
          <ReportRenderer blocks={blocks} />
        ) : (
          <div style={{ background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 8, padding: 32, textAlign: 'center' }}>
            <Text style={{ display: 'block', marginBottom: 16 }}>Report not available. Open from a chat session.</Text>
            <Link to="/">返回对话</Link>
          </div>
        )}
      </main>
    </div>
  )
}
