import { Layout, Typography } from 'antd'
import { useAnalysisStore } from '../stores/analysisStore'
import { isBusyPhase } from '../stores/analysisReducer'
import '../styles/global.css'

/**
 * Workbench page — three-column layout (TopBar + LeftRail + Center + RightRail).
 * This is the Phase 6 minimum-viable shell; per-component work (TopBar,
 * LeftRail, ConversationStream, RequirementCard, etc.) is built on top
 * in subsequent phase-6.x commits.
 */
export default function WorkbenchPage() {
  const phase = useAnalysisStore((s: { phase: import('../types/analysis').AnalysisPhase }) => s.phase)
  const busy = isBusyPhase(phase)

  return (
    <div className="workbench-shell">
      <Layout.Header
        style={{
          background: 'var(--ink)',
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          padding: '0 22px',
          fontFamily: 'var(--font-display)',
          letterSpacing: 0.3,
        }}
      >
        <Typography.Title
          level={4}
          style={{
            color: '#FFFFFF',
            margin: 0,
            fontFamily: 'var(--font-display)',
            fontSize: 17,
            fontWeight: 700,
          }}
        >
          ReportAgent
        </Typography.Title>
        <span
          style={{
            marginLeft: 14,
            color: 'rgba(255,255,255,.65)',
            fontFamily: 'var(--font-ui)',
            fontSize: 11,
            letterSpacing: 1.2,
            textTransform: 'uppercase',
          }}
        >
          工作台
        </span>
        <div style={{ flex: 1 }} />
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            color: 'rgba(255,255,255,.85)',
            fontSize: 12,
            fontFamily: 'var(--font-ui)',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: busy ? 'var(--amber)' : 'var(--green)',
              display: 'inline-block',
            }}
          />
          {busy ? '处理中' : '已连接'}
        </span>
      </Layout.Header>

      <div className="workbench-body">
        <aside
          className="workbench-rail workbench-rail--left"
          style={{
            background: 'var(--rail)',
            borderRight: '1px solid var(--line)',
            padding: 'var(--sp-l)',
            overflow: 'auto',
          }}
        >
          <Typography.Text
            style={{
              fontSize: 10,
              letterSpacing: 1.4,
              color: 'var(--muted)',
              textTransform: 'uppercase',
              fontWeight: 700,
            }}
          >
            工作上下文
          </Typography.Text>
          {/* LeftRail content: 新建分析 + 最近会话 + 收藏报告 — Phase 6.1 */}
        </aside>

        <main
          style={{
            padding: 'var(--sp-xl)',
            overflow: 'auto',
            background: 'var(--canvas)',
          }}
        >
          {/* Center: ConversationStream + RequirementCard + GenerationProgress + ReportPaper */}
          <Typography.Text style={{ color: 'var(--muted)' }}>
            当前 phase: <code>{phase}</code>
          </Typography.Text>
        </main>

        <aside
          className="workbench-rail workbench-rail--right"
          style={{
            background: 'var(--paper)',
            borderLeft: '1px solid var(--line)',
            padding: 'var(--sp-l)',
            overflow: 'auto',
          }}
        >
          {/* RightRail: 分析助手 + 折叠 Runtime */}
        </aside>
      </div>
    </div>
  )
}
