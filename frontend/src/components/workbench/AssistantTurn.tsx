import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  /** 过程面板（思考摘要 + 步骤），与正文同属这一条消息。 */
  trace?: ReactNode
  /** 助手这句话（markdown）。状态文案 / 闲聊回复 / 服务端回填的那句话都走这里。 */
  markdown?: string
  /** 结构化 body：追问选项、确认按钮、解析中、进度、错误（同一时刻只出一种）。 */
  children?: ReactNode
}

/**
 * 当轮**唯一**的助手消息外壳（2026-11-05，plan conversation-transcript-messages
 * 修订 A4）。
 *
 * 此前一轮里最多会同时存在「思考一条气泡 + 状态文案一条气泡 + 进度卡一张 +
 * 只读大卡一张」，用户看到的是「生成了一份还是两份」。现在全部收进这一个
 * 气泡：`TracePanel` 在上、状态/动作在下，结构化展示（报告）仍按需另起。
 */
export default function AssistantTurn({ trace = null, markdown = '', children = null }: Props) {
  return (
    <div className="wb-message wb-reveal">
      <div className="wb-msg-avatar agent">A</div>
      <div className="wb-bubble wb-ask-bubble">
        {trace}
        {markdown.trim() ? (
          <div className="wb-turn-text">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          </div>
        ) : null}
        {children}
      </div>
    </div>
  )
}
