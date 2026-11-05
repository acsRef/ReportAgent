import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { TimelineEntry } from '../../types/report'
import {
  looksLikeMarkdown,
  reasoningBlocks,
  thoughtSummary,
} from './reasoningText'

interface Props {
  entries: TimelineEntry[]
  /** 异常说明（中断 / 停止 / 重连 / 后台完成），来自消息上的 `traceNote`。 */
  note?: string | null
  /** 是否仍在执行。 */
  running: boolean
  /**
   * 默认展开（2026-11-05，plan reasoning-chain-display D3）。
   *
   * 当轮传 true（用户刚问完就能看见「它在想什么」），历史轮次不传（只留一行头）。
   * 用户点过折叠头之后以用户的为准——`autoOpen` 不会把用户收起来的面板又抢回去。
   */
  autoOpen?: boolean
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

/**
 * 「思考」块：折叠头一行 + 展开后的**模型原文思考链路**与执行步骤（2026-11-05 重塑）。
 *
 * 参考产品（用户 2026-11-05 截图）的形态是「已思考（用时 4 秒）」→ 展开读到推理过程本身，
 * 而不是「第几步做了什么」。所以这里：
 *
 *   ① 头部讲**时间**（执行中讲当前步骤）；
 *   ② 展开后先给思考链路（`reasoning_content` 原文，markdown 或纯文本分别渲染）；
 *   ③ 再给执行步骤（每步一行 + 结论），同一段原文不再在每步里重复一遍。
 *
 * 依然没有边框卡片：用户明确要求「一整个的、不要卡片、太占地方」。
 */
export default function TracePanel({
  entries,
  note = null,
  running,
  autoOpen = false,
}: Props) {
  const [touched, setTouched] = useState(false)
  const [manualOpen, setManualOpen] = useState(false)

  const failedAt = useMemo(
    () => entries.find((entry) => entry.status === 'error') ?? null,
    [entries],
  )
  const chain = useMemo(() => reasoningBlocks(entries), [entries])

  if (entries.length === 0 && !note) return null

  const open = touched ? manualOpen : autoOpen
  const summary = thoughtSummary(entries, running)

  return (
    <div className={`wb-think${running ? ' is-running' : ''}${failedAt ? ' is-failed' : ''}`}>
      <button
        type="button"
        className="wb-think-toggle"
        onClick={() => {
          setTouched(true)
          setManualOpen(!open)
        }}
        aria-expanded={open}
      >
        <span className="wb-think-arrow" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span>{summary}</span>
      </button>

      {note && <div className="wb-think-note">{note}</div>}

      {open && (
        <div className="wb-think-body">
          {chain.length > 0 && (
            <section className="wb-chain" aria-label="思考链路">
              <div className="wb-chain-label">思考链路</div>
              {chain.map((block, index) => (
                <div className="wb-chain-block" key={`${block.node}-${index}`}>
                  {/* 只有一个来源时不加节点名：否则就是「一个标题 + 一段话」的噪声 */}
                  {chain.length > 1 && <div className="wb-chain-node">{block.node}</div>}
                  {looksLikeMarkdown(block.text) ? (
                    <div className="wb-chain-markdown">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
                    </div>
                  ) : (
                    <p className="wb-chain-text">{block.text}</p>
                  )}
                </div>
              ))}
            </section>
          )}

          {entries.length > 0 && (
            <section className="wb-chain-steps" aria-label="执行步骤">
              {chain.length > 0 && <div className="wb-chain-label">执行步骤</div>}
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
            </section>
          )}
        </div>
      )}
    </div>
  )
}
