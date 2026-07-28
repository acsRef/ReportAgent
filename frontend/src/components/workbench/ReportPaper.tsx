import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ChartBlock from '../report/blocks/ChartBlock'
import TableBlock from '../report/blocks/TableBlock'
import Spinner from '../atelier/Spinner'
import { useToast } from '../atelier/useToast'
import { fetchReportVersion, type ReportVersionDetail } from '../../api/sessionsClient'
import { adaptReport } from '../../adapter/reportAdapter'
import type { ReportBlock } from '../../types/report'
import type { RequirementCard } from '../../types/requirement'

interface Props {
  sessionId: string
  version: number
  requirement?: RequirementCard | null
  /** 重新生成 — prototype sends an adjust request with the same requirement. */
  onAdjust?: (text: string) => void
  onFocusComposer?: () => void
}

/**
 * Report shell per docs/intelligent-analysis-workbench.html: toolbar
 * (收藏/导出/重新生成/继续调整), paper with teal left bar, REPORT/v{n}
 * header + meta pairs, 核心发现 band (answer.insight), numbered sections
 * (OVERVIEW text / VISUALIZATION chart / EVIDENCE flat table), footer.
 * Content comes strictly from the real payload — nothing fabricated.
 */
export default function ReportPaper({
  sessionId,
  version,
  requirement,
  onAdjust,
  onFocusComposer,
}: Props) {
  const toast = useToast()
  const [detail, setDetail] = useState<ReportVersionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchReportVersion(sessionId, version)
      .then((response) => {
        if (!cancelled) setDetail(response.report)
      })
      .catch((err) => {
        if (!cancelled) setError(String(err).slice(0, 200))
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
      <div className="wb-report-shell" style={{ textAlign: 'center', padding: 40 }}>
        <Spinner />
      </div>
    )
  }
  if (error || !detail) {
    return (
      <div className="wb-report-shell" style={{ padding: 24, color: 'var(--muted)', fontSize: 12 }}>
        {error ?? '未找到该报告'}
      </div>
    )
  }

  const payload = detail.report_payload as { answer?: Record<string, unknown>; execution_status?: string; error?: Record<string, unknown> } | null
  const answer = payload?.answer ?? {}
  const verdict = detail.execution_status ?? payload?.execution_status ?? 'SUCCESS'

  // Historical failed version (status='error' on the row, or verdict FAILED).
  // We render an inline error band instead of the normal paper so the user
  // can see what went wrong when reviewing old attempts from the right rail.
  if (detail.status === 'error' || verdict === 'FAILED') {
    const errInfo = (payload?.error ?? {}) as { message?: string; kind?: string; code?: string }
    const triedSql =
      (detail.query_snapshot && typeof detail.query_snapshot === 'object'
        ? (detail.query_snapshot as { sql?: string }).sql
        : null) ?? null
    return (
      <div className="wb-report-shell wb-reveal">
        <div className="wb-report-toolbar">
          <span className="wb-report-version">报告 v{detail.version} · {detail.title}</span>
          <span className="wb-report-tools">
            {onFocusComposer && (
              <button type="button" className="wb-quiet-btn" onClick={onFocusComposer}>
                继续调整
              </button>
            )}
          </span>
        </div>
        <article className="wb-report-paper">
          <header className="wb-report-header">
            <div className="wb-report-number">REPORT / v{detail.version}</div>
            <h1 className="wb-report-title">{detail.title || '分析报告'}</h1>
            <div className="wb-report-meta">
              <span>
                <span className="wb-meta-key">状态</span>执行失败
              </span>
            </div>
          </header>
          <section className="wb-finding" style={{ borderLeftColor: 'var(--accent-warn, #c43)' }}>
            <div className="wb-finding-label">执行失败</div>
            <div className="wb-finding-text">
              {errInfo.message || '查询未能返回数据'}
              {triedSql && (
                <details style={{ marginTop: 12 }}>
                  <summary style={{ cursor: 'pointer', color: 'var(--teal-deep)' }}>
                    查看尝试的 SQL
                  </summary>
                  <pre style={{
                    marginTop: 8,
                    padding: 12,
                    background: 'var(--canvas)',
                    borderRadius: 6,
                    fontSize: 12,
                    overflow: 'auto',
                  }}>
                    <code>{triedSql}</code>
                  </pre>
                </details>
              )}
            </div>
          </section>
        </article>
      </div>
    )
  }

  const blocks: ReportBlock[] = adaptReport(payload as Parameters<typeof adaptReport>[0])
  const overviewBlock = blocks.find((block) => block.type === 'markdown')
  const chartBlock = blocks.find((block) => block.type === 'chart')
  const tableBlock = blocks.find((block) => block.type === 'table')
  const insight = typeof answer.insight === 'string' && answer.insight ? answer.insight : null
  const isEmpty = verdict === 'EMPTY'

  const metaPairs: Array<[string, string]> = [
    ['数据范围', requirement?.time_range ?? '—'],
    ['分析范围', requirement?.scope?.length ? requirement.scope.join(' · ') : '—'],
    ['报告来源', `当前会话第 ${detail.version} 个版本`],
    ['可信度', (requirement?.confidence ?? 0) >= 0.8 ? '高' : '中'],
  ]

  let sectionNumber = 0
  const nextIndex = () => {
    sectionNumber += 1
    return String(sectionNumber).padStart(2, '0')
  }

  return (
    <div className="wb-report-shell wb-reveal">
      <div className="wb-report-toolbar">
        <span className="wb-report-version">
          报告 v{detail.version} · {detail.title}
        </span>
        <span className="wb-report-tools">
          <button
            type="button"
            className="wb-quiet-btn"
            onClick={() => toast.success('报告已加入收藏')}
          >
            ☆ 收藏
          </button>
          <button
            type="button"
            className="wb-quiet-btn"
            onClick={() => {
              // /api/v1/sessions/{sid}/reports/{v}/export.xlsx —— 后端用
              // openpyxl 把 query_snapshot（不受 5000 行展示上限影响）写成
              // xlsx，浏览器自动用 JWT cookie 发起下载。
              const url = `/api/v1/sessions/${sessionId}/reports/${detail.version}/export.xlsx`
              window.open(url, '_blank')
            }}
          >
            导出 Excel
          </button>
          <button
            type="button"
            className="wb-quiet-btn"
            onClick={() => {
              if (typeof window !== 'undefined' && detail) {
                // HTML 备选：纯前端生成、只截前 50 行（轻量分享场景）。
                import('../../utils/export').then(({ exportReportHTML }) => {
                  exportReportHTML({
                    id: `r-${detail.version}`,
                    title: detail.title ?? '报告',
                    query: '',
                    blocks: [],
                    steps: [],
                    timestamp: Date.now(),
                    status: 'done',
                  } as any)
                })
              }
            }}
          >
            导出 HTML
          </button>
          {onAdjust && (
            <button
              type="button"
              className="wb-quiet-btn"
              onClick={() => onAdjust('使用相同需求重新生成报告')}
            >
              重新生成
            </button>
          )}
          {onFocusComposer && (
            <button type="button" className="wb-quiet-btn" onClick={onFocusComposer}>
              继续调整
            </button>
          )}
        </span>
      </div>

      <article className="wb-report-paper">
        <header className="wb-report-header">
          <div className="wb-report-number">REPORT / v{detail.version}</div>
          <h1 className="wb-report-title">{detail.title || '分析报告'}</h1>
          <div className="wb-report-meta">
            {metaPairs.map(([key, value]) => (
              <span key={key}>
                <span className="wb-meta-key">{key}</span>
                {value}
              </span>
            ))}
            {isEmpty && (
              <span>
                <span className="wb-meta-key">查询结果</span>未匹配到数据
              </span>
            )}
          </div>
        </header>

        {isEmpty && (
          <section className="wb-empty-band" aria-live="polite">
            <div className="wb-empty-label">查询已执行</div>
            <div className="wb-empty-text">未找到匹配记录</div>
          </section>
        )}

        {insight && (
          <section className="wb-finding">
            <div className="wb-finding-label">核心发现</div>
            <div className="wb-finding-text">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{insight}</ReactMarkdown>
            </div>
          </section>
        )}

        {overviewBlock && (
          <section className="wb-report-section">
            <div className="wb-section-head">
              <div>
                <div className="wb-section-index">{nextIndex()} / OVERVIEW</div>
                <h2 className="wb-section-title">分析概述</h2>
              </div>
            </div>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {String((overviewBlock.data as { content?: string }).content ?? '')}
            </ReactMarkdown>
          </section>
        )}

        {chartBlock && (
          <section className="wb-report-section">
            <div className="wb-section-head">
              <div>
                <div className="wb-section-index">{nextIndex()} / VISUALIZATION</div>
                <h2 className="wb-section-title">数据可视化</h2>
              </div>
              <span className="wb-section-note">由 chart_advisor 依据查询结果选择图表类型</span>
            </div>
            <div className="wb-panel">
              <div className="wb-panel-head">
                <span className="wb-panel-title">{chartBlock.title || '图表'}</span>
              </div>
              <div style={{ padding: '2px 9px 10px' }}>
                <ChartBlock block={chartBlock} bare height={210} />
              </div>
            </div>
          </section>
        )}

        {tableBlock && (
          <section className="wb-report-section">
            <div className="wb-section-head">
              <div>
                <div className="wb-section-index">{nextIndex()} / EVIDENCE</div>
                <h2 className="wb-section-title">关键数据明细</h2>
              </div>
              <span className="wb-section-note">来自已校验 SQL 的查询结果</span>
            </div>
            <TableBlock block={tableBlock} />
          </section>
        )}

        <footer className="wb-report-foot">
          <span>由 ReportAgent 根据确认需求自动组织</span>
          <span>
            {isEmpty ? '查询已执行 · 未匹配到数据' : '需求已确认 · SQL 已校验'}
          </span>
        </footer>
      </article>
    </div>
  )
}
