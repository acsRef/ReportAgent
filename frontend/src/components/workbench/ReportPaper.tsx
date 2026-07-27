import { useEffect, useState } from 'react'
import { Text, Title } from '../atelier/Typography'
import Spinner from '../atelier/Spinner'
import Empty from '../atelier/Empty'
import Tag from '../atelier/Tag'
import { fetchReportVersion, type ReportVersionDetail } from '../../api/sessionsClient'
import { adaptReport } from '../../adapter/reportAdapter'
import type { ReportBlock } from '../../types/report'
import ReportRenderer from '../report/ReportRenderer'

interface Props {
  sessionId: string
  version: number
}

export default function ReportPaper({ sessionId, version }: Props) {
  const [report, setReport] = useState<ReportVersionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [blocks, setBlocks] = useState<ReportBlock[]>([])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchReportVersion(sessionId, version)
      .then((r) => {
        if (cancelled) return
        setReport(r.report)
        try {
          const adapted = adaptReport(r.report.report_payload as any)
          setBlocks(adapted)
        } catch {
          setBlocks([
            {
              id: 'raw',
              type: 'markdown',
              title: r.report.title,
              data: { content: JSON.stringify(r.report.report_payload, null, 2) },
            },
          ])
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e).slice(0, 200))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, version])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 32 }}>
        <Spinner />
      </div>
    )
  }
  if (error) {
    return <Empty description={error} />
  }
  if (!report) {
    return <Empty description="未找到该报告" />
  }

  return (
    <div
      style={{
        background: 'var(--paper)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-m)',
        padding: 'var(--sp-2xl)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <Title
          level={2}
          style={{
            fontFamily: 'var(--font-display)',
            color: 'var(--ink)',
            margin: 0,
            fontSize: 24,
          }}
        >
          {report.title}
        </Title>
        <Tag tone={report.status === 'done' ? 'teal' : 'amber'}>{report.status}</Tag>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--muted)' }}>
          v{report.version} · {new Date(report.created_at).toLocaleString('zh-CN')}
        </span>
      </div>
      <Text style={{ color: 'var(--muted)', fontSize: 12 }}>
        session: {report.session_id}
      </Text>
      <div style={{ marginTop: 18 }}>
        <ReportRenderer blocks={blocks} />
      </div>
    </div>
  )
}
