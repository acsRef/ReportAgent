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
 * 除当前轮以外的轮次按顺序铺成时间线，报告默认收起、点开才挂 ReportPaper
 * （每个 ReportPaper 自己按版本拉数，一屏挂十个会炸）。
 */
export default function ConversationTimeline({ sessionId, turns }: Props) {
  const [openKey, setOpenKey] = useState<string | null>(null)

  if (turns.length === 0) return null

  return (
    <section className="wb-history" aria-label="历史分析轮次">
      {turns.map((turn) => {
        const version = turn.version
        const isOpen = openKey === turn.key
        return (
          <div className="wb-history-turn" key={turn.key}>
            <UserBubble text={turn.question} />
            <div className="wb-history-report">
              {version ? (
                <>
                  <span className="wb-history-version">v{version.version}</span>
                  <span className="wb-history-title">{version.title || '报告'}</span>
                  <span className={`wb-history-status is-${version.status}`}>
                    {STATUS_LABEL[version.status] ?? version.status}
                  </span>
                  <button
                    type="button"
                    className="wb-quiet-btn"
                    onClick={() => setOpenKey(isOpen ? null : turn.key)}
                  >
                    {isOpen ? '收起报告' : '展开报告'}
                  </button>
                </>
              ) : (
                <span className="wb-history-status">未生成报告</span>
              )}
            </div>
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
