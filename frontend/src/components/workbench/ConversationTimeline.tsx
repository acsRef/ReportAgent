import { useState } from 'react'
import ReportPaper from './ReportPaper'
import { UserBubble } from './MessageBubbles'
import type { ReportVersion } from '../../types/analysis'

export interface TimelineTurn {
  key: string
  question: string
  /** 该轮产出的报告版本（chitchat / 失败轮可能没有）。 */
  version: ReportVersion | null
}

interface Props {
  sessionId: string
  turns: TimelineTurn[]
  /**
   * 最后一轮历史默认展开（2026-11-04，plan chat-like-conversation-flow）。
   *
   * 此前所有历史轮次默认收起：用户刚看到报告，再发一句追问，报告立刻被折成
   * 一行「v1 … 展开报告」——像内容凭空消失。现在**最近一轮保持展开**，更早的
   * 仍可折叠，渲染预算仍受控（最多多挂 1 个 ReportPaper）。
   */
  expandLatest?: boolean
}

const STATUS_LABEL: Record<string, string> = {
  done: '已完成',
  error: '执行失败',
  generating: '生成中',
}

/**
 * 历史轮次时间线。
 *
 * 2026-11-02：工作台画布此前只渲染「最后一次提问 + 一张卡 + 一个版本」，
 * 新提问会把旧问答顶掉，用户看到的是"以前的对话和报告都不见了"。这里把
 * 除当前轮以外的轮次按顺序铺成时间线（每个 ReportPaper 自己按版本拉数，
 * 一屏挂十个会炸，所以只有最近一轮默认展开）。
 */
export default function ConversationTimeline({
  sessionId,
  turns,
  expandLatest = true,
}: Props) {
  const [openKey, setOpenKey] = useState<string | null>(null)
  // 用户显式收起过的轮次（默认展开的最后一轮也要能收起）
  const [closedKey, setClosedKey] = useState<string | null>(null)

  if (turns.length === 0) return null

  const latestKey = turns[turns.length - 1]?.key ?? null

  function isOpenFor(key: string): boolean {
    if (openKey === key) return true
    if (closedKey === key) return false
    return expandLatest && key === latestKey
  }

  function toggle(key: string) {
    const open = isOpenFor(key)
    setOpenKey(open ? null : key)
    setClosedKey(open ? key : null)
  }

  return (
    <section className="wb-history" aria-label="历史分析轮次">
      {turns.map((turn) => {
        const version = turn.version
        const isOpen = isOpenFor(turn.key)
        const isLatest = turn.key === latestKey
        return (
          <div className="wb-history-turn" key={turn.key}>
            <UserBubble text={turn.question} />
            {/* 2026-11-04（plan history-turn-pairing）：没有报告的轮次只留提问气泡。
                此前这里渲染一条「未生成报告」的边框小条——没有可展开的内容，纯粹
                是噪声，而且它长在时间线里，视觉上直接粘在下一条助手消息上方。
                status=error 的版本仍然正常渲染并标红。 */}
            {version && (
              <div className="wb-history-report">
                <span className="wb-history-version">v{version.version}</span>
                <span className="wb-history-title">{version.title || '报告'}</span>
                <span className={`wb-history-status is-${version.status}`}>
                  {STATUS_LABEL[version.status] ?? version.status}
                </span>
                <button
                  type="button"
                  className="wb-quiet-btn"
                  onClick={() => toggle(turn.key)}
                >
                  {isOpen ? '收起报告' : `查看 v${version.version} 报告`}
                </button>
                {isOpen && isLatest && expandLatest && (
                  <span className="wb-history-note">上一轮结果，默认展开</span>
                )}
              </div>
            )}
            {isOpen && version && (
              <ReportPaper
                key={`${sessionId}-${version.version}`}
                sessionId={sessionId}
                version={version.version}
              />
            )}
          </div>
        )
      })}
    </section>
  )
}
