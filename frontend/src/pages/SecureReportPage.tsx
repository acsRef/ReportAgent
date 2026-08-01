import { useParams } from 'react-router-dom'
import { Title } from '../components/atelier/Typography'
import ReportPaper from '../components/workbench/ReportPaper'
import '../styles/global.css'
import '../styles/workbench.css'

/**
 * 报告分享/安全查看页。
 *
 * 直接复用工作台的 ReportPaper——报告按设计稿渲染（纸面、REPORT/v{n} 刊头、
 * 核心发现、编号分节、失败归档带），而不是把 payload 当 JSON 裸 dump。
 * ReportPaper 自行负责取数与 loading/error/三态渲染。
 */
export default function SecureReportPage() {
  const { sessionId, version } = useParams<{ sessionId: string; version: string }>()
  const versionNum = Number(version)
  const valid = Boolean(sessionId) && !Number.isNaN(versionNum)

  return (
    <div style={{ minHeight: '100vh', background: 'var(--canvas)', display: 'flex', flexDirection: 'column' }}>
      <header
        style={{
          background: 'var(--ink)',
          color: 'var(--on-ink)',
          padding: '0 22px',
          display: 'flex',
          alignItems: 'center',
          height: 48,
          boxShadow: 'var(--shadow-topbar)',
        }}
      >
        <Title
          level={4}
          style={{ color: 'var(--on-ink)', margin: 0, fontFamily: 'var(--font-display)', fontSize: 16 }}
        >
          ReportAgent — 报告 v{version}
        </Title>
      </header>
      <main style={{ padding: 'var(--sp-xl)', flex: 1, width: '100%', maxWidth: 1080, margin: '0 auto' }}>
        {valid ? (
          <ReportPaper sessionId={sessionId as string} version={versionNum} />
        ) : (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
            无效的报告链接
          </div>
        )}
      </main>
    </div>
  )
}
