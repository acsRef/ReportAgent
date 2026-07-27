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

/** Agent chat bubble (left-aligned, paper) — copy is markdown. */
export function AgentBubble({ markdown }: { markdown: string }) {
  return (
    <div className="wb-message wb-reveal">
      <div className="wb-msg-avatar agent">A</div>
      <div className="wb-bubble">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
      </div>
    </div>
  )
}
