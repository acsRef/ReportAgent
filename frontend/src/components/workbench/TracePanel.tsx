import { useMemo, useState } from 'react'
import type { TimelineEntry } from '../../types/report'

interface Props {
  entries: TimelineEntry[]
  /** 异常说明（中断 / 停止 / 重连 / 后台完成），来自 reducer 的 `timelineNote`。 */
  note?: string | null
  /** 是否仍在执行。 */
  running: boolean
}

const STATUS_ICON: Record<string, string> = {
  running: '◐',
  success: '✓',
  error: '✕',
  pending: '⏸',
}

const STATUS_TEXT: Record<string, string> = {
  running: '进行中',
  success: '完成',
  error: '失败',
  pending: '已停止',
}

function totalText(entries: TimelineEntry[]): string {
  if (entries.length < 2) return ''
  const ms = Math.max(0, entries[entries.length - 1].timestamp - entries[0].timestamp)
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

/**
 * 「思考过程」= 助手消息里的**一行摘要**，展开才看步骤（2026-11-04）。
 *
 * 用户明确要求：「应该是一整个的，不要用卡片的方式，太占地方了」——
 * 所以这里没有边框卡片、没有左缩进，只有一行 `▸ 思考了 6 步 · 14s`；
 * 展开后是紧凑的步骤列表（每步一行，带结论）。
 */
export default function TracePanel({ entries, note = null, running }: Props) {
  const [open, setOpen] = useState(false)

  const failedAt = useMemo(
    () => entries.find((entry) => entry.status === 'error') ?? null,
    [entries],
  )
  const stoppedAt = useMemo(
    () => entries.find((entry) => entry.status === 'pending') ?? null,
    [entries],
  )
  const activeStep = useMemo(
    () => [...entries].reverse().find((entry) => entry.status === 'running') ?? null,
    [entries],
  )

  if (entries.length === 0 && !note) return null

  const summary = running
    ? `正在思考 · ${activeStep?.nodeName ?? '准备中'}`
    : failedAt
      ? `思考中断于「${failedAt.nodeName}」`
      : stoppedAt
        ? `已停止于「${stoppedAt.nodeName}」（后台继续）`
        : `思考了 ${entries.length} 步${totalText(entries) ? ` · ${totalText(entries)}` : ''}`

  return (
    <div className={`wb-think${running ? ' is-running' : ''}`}>
      <button
        type="button"
        className="wb-think-toggle"
        onClick={() => setOpen((cur) => !cur)}
        aria-expanded={open}
      >
        <span className="wb-think-arrow" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span>{summary}</span>
      </button>

      {note && <div className="wb-think-note">{note}</div>}

      {open && entries.length > 0 && (
        <ol className="wb-think-list">
          {entries.map((entry, index) => (
            <li key={`${entry.nodeName}-${index}`} className="wb-think-step">
              <span className={`wb-think-icon is-${entry.status}`} aria-hidden="true">
                {STATUS_ICON[entry.status] ?? '·'}
              </span>
              <span className="wb-think-name">{entry.nodeName}</span>
              <span className="wb-think-status">{STATUS_TEXT[entry.status] ?? ''}</span>
              {entry.detail && <span className="wb-think-detail">{entry.detail}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
