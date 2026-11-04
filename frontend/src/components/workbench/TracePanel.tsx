import { useMemo, useState } from 'react'
import type { TimelineEntry } from '../../types/report'

interface Props {
  entries: TimelineEntry[]
  /** 异常说明（中断 / 停止 / 重连 / 后台完成），来自 reducer 的 `timelineNote`。 */
  note?: string | null
  /** 是否仍在执行（决定默认展开与头部文案）。 */
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

const KIND_TEXT: Record<string, string> = {
  agent: '理解',
  tool: '取数',
  sql: '查询',
  repair: '修复',
  report: '成文',
}

/** 相邻步骤时间戳差 → 该步耗时（trace 帧只带到达时间，够用且诚实标 ≈）。 */
function durations(entries: TimelineEntry[]): number[] {
  return entries.map((entry, index) => {
    const next = entries[index + 1]
    if (!next || entry.status === 'running') return 0
    return Math.max(0, next.timestamp - entry.timestamp)
  })
}

function totalMs(entries: TimelineEntry[]): number {
  if (entries.length < 2) return 0
  return Math.max(0, entries[entries.length - 1].timestamp - entries[0].timestamp)
}

/** 总耗时文案：<1s 用 ms（步骤常在毫秒级完成，0.0s 读起来像没跑）。 */
function totalText(entries: TimelineEntry[]): string {
  const ms = totalMs(entries)
  return ms < 1000 ? `${Math.max(0, Math.round(ms))}ms` : `${(ms / 1000).toFixed(1)}s`
}

/**
 * 「执行过程」面板（2026-11-04，plan trace-visible-reasoning）。
 *
 * 用户要的是**看得见 Agent 在做什么/为什么**——不是模型 CoT。这里把既有的
 * `trace` 帧（安全检查 → 识别意图 → 解析需求 → 口径预览 → 保存需求卡 →
 * 规划 → 生成 SQL → 校验 → 执行 → 评估 → 诊断修复 → 成文）按步骤铺开，
 * 并显式表达异常：中断 / 停止 / 失败 / 重连都在头部说清楚，不让人对着转圈猜。
 */
export default function TracePanel({ entries, note = null, running }: Props) {
  const [manualOpen, setManualOpen] = useState<boolean | null>(null)
  const steps = durations(entries)

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

  // 运行中 / 有异常说明 / 中途失败 → 自动展开（这三种情况用户最需要看细节）；
  // 正常结束的轮次默认收起成一行摘要（豆包式），用户手动开/关过就以用户为准。
  const open = manualOpen ?? (running || Boolean(note) || Boolean(failedAt))

  const header = running
    ? `正在执行 · ${activeStep?.nodeName ?? '准备中'}`
    : failedAt
      ? `执行过程 · 中断于「${failedAt.nodeName}」`
      : stoppedAt
        ? `执行过程 · 已停止于「${stoppedAt.nodeName}」（后台继续）`
        : `执行过程 · ${entries.length} 步 · ${totalText(entries)}`

  return (
    <section className={`wb-trace${running ? ' is-running' : ''}`} aria-label="执行过程">
      <button
        type="button"
        className="wb-trace-head"
        onClick={() => setManualOpen(!open)}
        aria-expanded={open}
      >
        <span className="wb-trace-dot" aria-hidden="true" />
        <span className="wb-trace-title">{header}</span>
        <span className="wb-trace-toggle">{open ? '收起' : '展开'}</span>
      </button>

      {note && <div className="wb-trace-note">{note}</div>}

      {open && entries.length > 0 && (
        <ol className="wb-trace-list">
          {entries.map((entry, index) => (
            <li
              key={`${entry.nodeName}-${index}`}
              className={`wb-trace-step is-${entry.status}`}
            >
              <span className="wb-trace-icon" aria-hidden="true">
                {STATUS_ICON[entry.status] ?? '·'}
              </span>
              <span className="wb-trace-step-body">
                <span className="wb-trace-step-line">
                  <span className="wb-trace-step-name">{entry.nodeName}</span>
                  {entry.kind && (
                    <span className="wb-trace-kind">{KIND_TEXT[entry.kind] ?? entry.kind}</span>
                  )}
                  <span className="wb-trace-meta">
                    {entry.status === 'running'
                      ? STATUS_TEXT.running
                      : steps[index] > 0
                        ? `≈${(steps[index] / 1000).toFixed(1)}s`
                        : (STATUS_TEXT[entry.status] ?? '')}
                  </span>
                </span>
                {/* 这一步的**结论**（意图 / 口径 / 行数 / 失败原因）——用户要的「思考内容」 */}
                {entry.detail && <span className="wb-trace-detail">{entry.detail}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
