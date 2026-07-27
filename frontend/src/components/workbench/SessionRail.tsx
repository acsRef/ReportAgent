import { useState } from 'react'
import Modal from '../atelier/Modal'
import Spinner from '../atelier/Spinner'
import { Text } from '../atelier/Typography'
import { relativeTime, statusPill } from './sessionMeta'
import type { AnalysisPhase, ReportVersion, SessionSummary } from '../../types/analysis'

interface Props {
  sessions: SessionSummary[]
  activeSessionId: string | null
  /** Live report versions of the active session (from the store). */
  reportVersions: ReportVersion[]
  selectedReportVersion: number | null
  loading?: boolean
  onSelect: (sessionId: string) => void
  onSelectVersion: (version: number) => void
  onNew: () => void
}

const DAY = 86_400_000

function sessionTitle(session: SessionSummary): string {
  return (
    session.title ||
    (session as { first_message?: string }).first_message ||
    session.session_id.slice(0, 8)
  )
}

/** Left rail per the prototype: 新建分析, bucketed sessions with status
 *  pills + relative times, version-box under the active session. */
export default function SessionRail({
  sessions,
  activeSessionId,
  reportVersions,
  selectedReportVersion,
  loading,
  onSelect,
  onSelectVersion,
  onNew,
}: Props) {
  const [showAllVersions, setShowAllVersions] = useState(false)
  const now = Date.now()

  const today: SessionSummary[] = []
  const pastWeek: SessionSummary[] = []
  const older: SessionSummary[] = []
  for (const session of sessions) {
    const parsed = Date.parse(session.updated_at)
    const age = Number.isFinite(parsed) ? (now - parsed) / DAY : Infinity
    if (age < 1) today.push(session)
    else if (age < 7) pastWeek.push(session)
    else older.push(session)
  }

  const renderVersionButton = (version: ReportVersion) => {
    const current = version.version === selectedReportVersion
    return (
      <button
        key={version.version}
        type="button"
        className={current ? 'wb-version-btn current' : 'wb-version-btn'}
        onClick={() => onSelectVersion(version.version)}
      >
        <span className="wb-version-dot" />
        <span>
          v{version.version} · {version.title}
        </span>
        <span className="wb-version-meta">
          {current ? '当前' : relativeTime(version.created_at, now)}
        </span>
      </button>
    )
  }

  const renderBucket = (label: string, bucket: SessionSummary[]) => {
    if (bucket.length === 0) return null
    return (
      <div key={label}>
        <div className="wb-group-label">{label}</div>
        {bucket.map((session) => {
          const active = session.session_id === activeSessionId
          const pill = statusPill((session.phase ?? 'idle') as AnalysisPhase)
          const title = sessionTitle(session)
          return (
            <div key={session.session_id} className={active ? 'wb-session active' : 'wb-session'}>
              <button
                type="button"
                className="wb-session-main"
                onClick={() => onSelect(session.session_id)}
              >
                <span className="wb-session-icon">{title.slice(0, 1)}</span>
                <span>
                  <span className="wb-session-title">{title}</span>
                  <span className={pill.cls ? `wb-status-pill ${pill.cls}` : 'wb-status-pill'}>
                    {pill.text}
                  </span>
                  {active && (
                    <div className="wb-session-sub">
                      {session.msg_count} 条消息 · {reportVersions.length} 个报告版本
                    </div>
                  )}
                </span>
                <span className="wb-session-time">{relativeTime(session.updated_at, now)}</span>
              </button>
              {active && reportVersions.length > 0 && (
                <div className="wb-version-box">
                  <div className="wb-version-caption">
                    <span>报告版本</span>
                    <span>仅回看，不重新生成</span>
                  </div>
                  {reportVersions.slice(-3).reverse().map(renderVersionButton)}
                  {reportVersions.length > 3 && (
                    <button
                      type="button"
                      className="wb-version-more"
                      onClick={() => setShowAllVersions(true)}
                    >
                      查看全部版本 →
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <aside
      className="workbench-rail workbench-rail--left"
      style={{
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
        overflow: 'hidden',
        background: 'var(--rail)',
        borderRight: '1px solid var(--line-2)',
      }}
    >
      <div className="wb-left-head">
        <button type="button" className="wb-new-btn" onClick={onNew}>
          ＋ 新建分析
        </button>
      </div>

      <div className="wb-session-scroll">
        {loading ? (
          <div style={{ textAlign: 'center', padding: 8 }}>
            <Spinner size="sm" />
          </div>
        ) : sessions.length === 0 ? (
          <Text style={{ color: 'var(--faint)', fontSize: 12 }}>暂无会话</Text>
        ) : (
          <>
            {renderBucket('今天', today)}
            {renderBucket('过去 7 天', pastWeek)}
            {renderBucket('更早', older)}
          </>
        )}
      </div>

      <div className="wb-left-footer">
        对话会自动保存 · 当前 {sessions.length} 个分析任务
      </div>

      <Modal
        open={showAllVersions}
        onClose={() => setShowAllVersions(false)}
        title="全部报告版本"
        footer={null}
      >
        <div style={{ display: 'grid', gap: 6 }}>
          {[...reportVersions].reverse().map((version) => (
            <button
              key={version.version}
              type="button"
              className={
                version.version === selectedReportVersion
                  ? 'wb-version-btn current'
                  : 'wb-version-btn'
              }
              style={{ position: 'static' }}
              onClick={() => {
                onSelectVersion(version.version)
                setShowAllVersions(false)
              }}
            >
              <span className="wb-version-dot" />
              <span>
                v{version.version} · {version.title}
              </span>
              <span className="wb-version-meta">{relativeTime(version.created_at, now)}</span>
            </button>
          ))}
        </div>
      </Modal>
    </aside>
  )
}
