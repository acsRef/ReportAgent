import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** User chat bubble (right-aligned, ink background) — prototype .message.user */
export function UserBubble({ text }: { text: string }) {
  return (
    <div className="wb-message user wb-reveal">
      <div className="wb-bubble">{text}</div>
      <div className="wb-msg-avatar">我</div>
    </div>
  )
}

/**
 * Agent chat bubble (left-aligned, paper) — copy is markdown.
 *
 * `trace`：2026-11-05 起「思考过程」并进这一条消息（同一气泡内的第一行），
 * 之前「思考一条 + 状态文案一条」会让一轮里冒出两个助手气泡（用户：「生成了两份」）。
 */
export function AgentBubble({ markdown, trace }: { markdown: string; trace?: ReactNode }) {
  return (
    <div className="wb-message wb-reveal">
      <div className="wb-msg-avatar agent">A</div>
      <div className={trace ? 'wb-bubble wb-ask-bubble' : 'wb-bubble'}>
        {trace}
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
      </div>
    </div>
  )
}
