import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Text, Title } from '../components/atelier/Typography'
import { fetchReportVersion, type ReportVersionDetail } from '../api/sessionsClient'
import Spinner from '../components/atelier/Spinner'
import '../styles/global.css'

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
    <div style={{ minHeight: '100vh', background: 'var(--canvas)', display: 'flex', flexDirection: 'column' }}>
      <header
        style={{
          background: 'var(--ink)',
          color: '#FFFFFF',
          padding: '0 22px',
          display: 'flex',
          alignItems: 'center',
          height: 48,
        }}
      >
        <Title
          level={4}
          style={{ color: '#FFFFFF', margin: 0, fontFamily: 'var(--font-display)', fontSize: 16 }}
        >
          ReportAgent — 报告 v{version}
        </Title>
      </header>
      <main style={{ padding: 'var(--sp-xl)', flex: 1 }}>
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
            <Spinner />
          ) : error ? (
            <Text type="danger">{error}</Text>
          ) : report ? (
            <>
              <Title
                level={2}
                style={{ fontFamily: 'var(--font-display)', color: 'var(--ink)' }}
              >
                {report.title}
              </Title>
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
            <Text type="secondary">未找到该报告版本</Text>
          )}
        </div>
      </main>
    </div>
  )
}
