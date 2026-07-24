import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Layout, Spin, Typography } from 'antd'
import { fetchReportVersion, type ReportVersionDetail } from '../api/sessionsClient'
import '../styles/global.css'

/**
 * Authenticated report page — reads `agent.report_version` for the given
 * (session, version) tuple via `GET /api/v1/sessions/{sid}/reports/{v}`.
 * No LLM, no graph; just a pure DB read.
 */
export default function SecureReportPage() {
  const { sessionId, version } = useParams<{ sessionId: string; version: string }>()
  const [report, setReport] = useState<ReportVersionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    if (!sessionId || !version) return
    setLoading(true)
    fetchReportVersion(sessionId, Number(version))
      .then((r) => {
        if (!cancelled) setReport(r.report)
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

  return (
    <Layout style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
      <Layout.Header
        style={{
          background: 'var(--ink)',
          color: '#FFFFFF',
          padding: '0 22px',
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <Typography.Title
          level={4}
          style={{ color: '#FFFFFF', margin: 0, fontFamily: 'var(--font-display)' }}
        >
          ReportAgent — 报告 v{version}
        </Typography.Title>
      </Layout.Header>
      <Layout.Content style={{ padding: 'var(--sp-xl)' }}>
        <div
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            boxShadow: 'var(--shadow-card)',
            padding: 'var(--sp-2xl)',
            maxWidth: 1080,
            margin: '0 auto',
          }}
        >
          {loading ? (
            <Spin />
          ) : error ? (
            <Typography.Text type="danger">{error}</Typography.Text>
          ) : report ? (
            <>
              <Typography.Title
                level={2}
                style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}
              >
                {report.title}
              </Typography.Title>
              <pre
                style={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  color: 'var(--ink-2)',
                  background: 'var(--canvas)',
                  padding: 'var(--sp-l)',
                  borderRadius: 'var(--r-s)',
                  border: '1px solid var(--line)',
                }}
              >
                {JSON.stringify(report.report_payload, null, 2)}
              </pre>
            </>
          ) : (
            <Typography.Text type="secondary">未找到该报告版本</Typography.Text>
          )}
        </div>
      </Layout.Content>
    </Layout>
  )
}
