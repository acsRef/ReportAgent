import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Empty from '../components/atelier/Empty'
import Spinner from '../components/atelier/Spinner'
import Tag from '../components/atelier/Tag'
import { Text, Title } from '../components/atelier/Typography'
import { IconClock, IconFund, IconReload } from '../components/ui/Icons'
import {
  fetchMetrics,
  fetchTraceDetail,
  fetchTraces,
  type LlmCallDetail,
  type Metrics,
  type SpanDetail,
  type TraceDetail,
  type TraceSummary,
} from '../api/observabilityClient'
import '../styles/observability.css'

type Tone = 'green' | 'red' | 'amber' | 'default'

function statusTone(status: string): Tone {
  if (status === 'SUCCESS' || status === 'DONE') return 'green'
  if (status === 'FAILED' || status === 'REJECTED') return 'red'
  if (status === 'RUNNING') return 'amber'
  return 'default'
}

/** 状态分布条的着色：完成=绿、失败=红、运行=青、等待=琥珀、其余=灰。只用 tokens。 */
function statusBarColor(status: string): string {
  if (status === 'DONE' || status === 'SUCCESS') return 'var(--green)'
  if (status === 'FAILED' || status === 'REJECTED') return 'var(--red)'
  if (status === 'RUNNING') return 'var(--teal)'
  if (status.startsWith('AWAITING')) return 'var(--amber)'
  return 'var(--dot)'
}

function statusDotColor(status: string): string {
  if (status === 'DONE' || status === 'SUCCESS') return 'var(--green)'
  if (status === 'FAILED' || status === 'REJECTED') return 'var(--red)'
  if (status === 'RUNNING') return 'var(--teal)'
  return 'var(--amber)'
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

function fmtNum(n: number | null): string {
  return n == null ? '—' : n.toLocaleString('zh-CN')
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 11, color: 'var(--faint)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</span>
      <span style={{ font: '600 17px var(--font-display)', color: 'var(--ink)' }}>{value}</span>
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const running = status === 'RUNNING'
  return (
    <span
      className={running ? 'obs-pulse' : undefined}
      style={{
        width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
        background: statusDotColor(status), display: 'inline-block',
      }}
      aria-hidden="true"
    />
  )
}

function SpanRow({ span, maxDuration }: { span: SpanDetail; maxDuration: number }) {
  const [open, setOpen] = useState(false)
  const pct = maxDuration > 0 && span.duration_ms != null ? (span.duration_ms / maxDuration) * 100 : 0
  return (
    <div style={{ position: 'relative', paddingLeft: 20 }}>
      <span style={{ position: 'absolute', left: 5, top: 0, bottom: -12, width: 1, background: 'var(--line-2)' }} />
      <span
        style={{
          position: 'absolute', left: 1, top: 6, width: 9, height: 9, borderRadius: '50%',
          background: span.status === 'FAILED' ? 'var(--red)' : 'var(--done-dot)',
          border: '2px solid var(--paper)',
        }}
      />
      <div
        className="obs-span"
        onClick={() => setOpen((v) => !v)}
        style={{
          cursor: 'pointer', padding: '8px 12px', marginBottom: 8,
          background: 'var(--paper-2)', border: '1px solid var(--row-line)', borderRadius: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Text strong style={{ fontSize: 13, color: 'var(--ink)', fontFamily: 'var(--font-mono)' }}>
            {span.span_name}
          </Text>
          <Tag tone="ink">{span.span_type}</Tag>
          <Tag tone={statusTone(span.status)}>{span.status}</Tag>
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            {fmtDuration(span.duration_ms)}
          </span>
        </div>
        <div style={{ marginTop: 6, height: 4, borderRadius: 2, background: 'var(--track)' }}>
          <div className="obs-bar-fill" style={{ width: `${Math.max(pct, 2)}%`, height: '100%', borderRadius: 2, background: 'var(--teal)' }} />
        </div>
        {span.error && (
          <div style={{ marginTop: 6, fontSize: 12, color: 'var(--red)' }}>错误：{span.error}</div>
        )}
        {open && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--muted)' }}>
            <div style={{ marginBottom: 4 }}>span_id: <code style={{ fontFamily: 'var(--font-mono)' }}>{span.span_id}</code></div>
            <div>开始：{fmtTime(span.start_time)}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function TraceDetailPanel({ detail }: { detail: TraceDetail }) {
  const maxDuration = Math.max(1, ...detail.spans.map((s) => s.duration_ms ?? 0))
  return (
    <div className="obs-detail" style={{ padding: '16px 20px', background: 'var(--canvas)', borderBottomLeftRadius: 8, borderBottomRightRadius: 8 }}>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 16 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          trace_id: <code style={{ fontFamily: 'var(--font-mono)' }}>{detail.trace.trace_id}</code>
        </Text>
        <Text type="secondary" style={{ fontSize: 12 }}>会话: {detail.trace.session_id ?? '—'}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>总耗时: {fmtDuration(detail.trace.total_duration_ms)}</Text>
      </div>

      <Text strong style={{ fontSize: 12, color: 'var(--muted)', letterSpacing: '0.08em', display: 'block', marginBottom: 12 }}>
        AGENT 执行链路（{detail.spans.length} 步）
      </Text>
      {detail.spans.length === 0 ? (
        <Text type="secondary" style={{ fontSize: 13 }}>无 span 记录</Text>
      ) : (
        <div>{detail.spans.map((s) => <SpanRow key={s.span_id} span={s} maxDuration={maxDuration} />)}</div>
      )}

      <Text strong style={{ fontSize: 12, color: 'var(--muted)', letterSpacing: '0.08em', display: 'block', margin: '16px 0 10px' }}>
        LLM 调用（{detail.llm_calls.length} 次）
      </Text>
      {detail.llm_calls.length === 0 ? (
        <Text type="secondary" style={{ fontSize: 13 }}>无 LLM 调用记录</Text>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {detail.llm_calls.map((c: LlmCallDetail, i) => (
            <div
              key={i}
              className="obs-lift"
              style={{
                display: 'flex', gap: 16, alignItems: 'center', padding: '8px 12px',
                background: 'var(--paper)', border: '1px solid var(--row-line)', borderRadius: 8, fontSize: 12,
              }}
            >
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink)' }}>{c.model || '—'}</span>
              <span style={{ color: 'var(--muted)' }}>tokens {fmtNum(c.prompt_tokens + c.completion_tokens)}</span>
              <span style={{ color: 'var(--muted)' }}>延迟 {fmtDuration(c.latency_ms)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ObservabilityPage() {
  const navigate = useNavigate()
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [m, t] = await Promise.all([fetchMetrics(), fetchTraces(50)])
      setMetrics(m)
      setTraces(t.traces)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const selectTrace = async (id: string) => {
    if (selectedId === id) {
      setSelectedId(null)
      setDetail(null)
      return
    }
    setSelectedId(id)
    setDetailLoading(true)
    setDetail(null)
    try {
      setDetail(await fetchTraceDetail(id))
    } catch (e) {
      setError(String(e))
    } finally {
      setDetailLoading(false)
    }
  }

  const successPct = metrics?.success_rate != null ? `${(metrics.success_rate * 100).toFixed(1)}%` : '—'

  // 状态分布（用于完成率下方的堆叠条 + 图例）
  const statusSegments = (() => {
    const entries = Object.entries(metrics?.status_breakdown ?? {})
    const total = entries.reduce((s, [, n]) => s + n, 0)
    if (total === 0) return []
    return entries.map(([status, count]) => ({
      status, count, pct: (count / total) * 100, color: statusBarColor(status),
    }))
  })()

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--canvas)', overflow: 'auto' }}>
      <div style={{ maxWidth: 1080, margin: '0 auto', width: '100%', padding: 32 }}>
        {/* 页头 */}
        <div className="obs-fade" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <Title level={4} style={{ margin: 0, color: 'var(--ink)' }}>
              <IconFund style={{ marginRight: 8 }} />
              可观测性
            </Title>
            <Text type="secondary" style={{ fontSize: 13, color: 'var(--muted)' }}>
              系统指标与 Agent 执行链路追踪
            </Text>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="nav-btn" onClick={() => load()}>
              <IconReload style={{ marginRight: 6 }} />刷新
            </button>
            <button type="button" className="nav-btn" onClick={() => navigate('/')}>返回工作台</button>
          </div>
        </div>

        {error && (
          <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 8, background: 'var(--red-soft)', color: 'var(--red)', fontSize: 13 }}>
            加载失败：{error}
          </div>
        )}

        {loading ? (
          <div style={{ marginTop: 80, display: 'flex', justifyContent: 'center' }}>
            <Spinner label="加载中…" />
          </div>
        ) : (
          <>
            {/* 系统总览带：大号总数 + 完成率（配状态分布条）+ 耗时 + LLM */}
            <div
              className="obs-fade obs-lift"
              style={{
                background: 'var(--paper)', border: '1px solid var(--line-2)', borderRadius: 12,
                boxShadow: 'var(--shadow-card)', padding: '22px 26px', marginBottom: 28,
                display: 'flex', flexWrap: 'wrap', alignItems: 'stretch',
              }}
            >
              <div style={{ paddingRight: 28, marginRight: 28, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                <span style={{ font: '700 11px var(--font-ui)', color: 'var(--muted)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>Trace 总数</span>
                <span style={{ font: '700 42px var(--font-display)', color: 'var(--ink)', lineHeight: 1.05 }}>{fmtNum(metrics?.trace_total ?? 0)}</span>
                <span style={{ fontSize: 12, color: 'var(--faint)' }}>已记录请求</span>
              </div>

              <div style={{ paddingRight: 28, marginRight: 28, borderRight: '1px solid var(--line)', flex: 1, minWidth: 220, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
                  <span style={{ font: '700 11px var(--font-ui)', color: 'var(--muted)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>完成率</span>
                  <span style={{ font: '700 30px var(--font-display)', color: 'var(--green)' }}>{successPct}</span>
                </div>
                <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', background: 'var(--track)' }}>
                  {statusSegments.map((seg) => (
                    <div key={seg.status} className="obs-bar-fill" title={`${seg.status} ${seg.count}`} style={{ width: `${seg.pct}%`, background: seg.color }} />
                  ))}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                  {statusSegments.map((seg) => (
                    <span key={seg.status} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--muted)' }}>
                      <span style={{ width: 7, height: 7, borderRadius: 2, background: seg.color, display: 'inline-block' }} />
                      {seg.status} {seg.count}
                    </span>
                  ))}
                </div>
              </div>

              <div style={{ paddingRight: 28, marginRight: 28, borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10 }}>
                <MiniStat label="平均耗时" value={fmtDuration(metrics?.avg_duration_ms ?? null)} />
                <MiniStat label="P95 耗时" value={fmtDuration(metrics?.p95_duration_ms ?? null)} />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 10 }}>
                <MiniStat label="LLM 调用" value={fmtNum(metrics?.llm_call_total ?? 0)} />
                <MiniStat label="LLM Tokens" value={fmtNum(metrics?.llm_tokens_total ?? 0)} />
              </div>
            </div>

            {/* Trace 列表 */}
            <Text strong style={{ fontSize: 12, color: 'var(--muted)', letterSpacing: '0.08em', display: 'block', marginBottom: 12 }}>
              最近 TRACE（{traces.length}）
            </Text>
            {traces.length === 0 ? (
              <Empty description={<span style={{ fontSize: 13, color: 'var(--muted)' }}>暂无 trace 记录</span>} style={{ marginTop: 40 }} />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column' }}>
                {traces.map((t) => {
                  const active = selectedId === t.trace_id
                  return (
                    <div key={t.trace_id} style={{ marginBottom: 8 }}>
                      <div
                        className="obs-lift"
                        onClick={() => selectTrace(t.trace_id)}
                        style={{
                          cursor: 'pointer', background: active ? 'var(--teal-pale)' : 'var(--paper)',
                          border: `1px solid ${active ? 'var(--teal)' : 'var(--line)'}`,
                          borderRadius: active ? '8px 8px 0 0' : 8,
                          padding: '12px 18px', display: 'flex', alignItems: 'center', gap: 14,
                        }}
                      >
                        <StatusDot status={t.status} />
                        <Tag tone={statusTone(t.status)}>{t.status}</Tag>
                        <span style={{ flex: 1, minWidth: 0, fontSize: 14, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {t.user_query || '(无查询文本)'}
                        </span>
                        <span style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>{t.trace_id.slice(0, 8)}</span>
                        <span style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'var(--font-mono)', minWidth: 70, textAlign: 'right' }}>
                          {fmtDuration(t.total_duration_ms)}
                        </span>
                        <span style={{ fontSize: 12, color: 'var(--faint)', minWidth: 150, textAlign: 'right' }}>
                          <IconClock style={{ marginRight: 4, verticalAlign: '-2px' }} />
                          {fmtTime(t.start_time)}
                        </span>
                      </div>
                      {active && (detailLoading ? (
                        <div style={{ padding: 24, background: 'var(--canvas)', borderBottomLeftRadius: 8, borderBottomRightRadius: 8, display: 'flex', justifyContent: 'center' }}>
                          <Spinner size="sm" label="加载链路…" />
                        </div>
                      ) : detail ? (
                        <TraceDetailPanel detail={detail} />
                      ) : null)}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
