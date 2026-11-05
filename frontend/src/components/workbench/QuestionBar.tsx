import type { ReactNode } from 'react'
import type { RequirementMissingField } from '../../types/requirement'

interface Props {
  field: RequirementMissingField
  /** 选中某个选项（服务端 PATCH 后由新卡推导下一个问题）。 */
  onPick: (key: string, value: string | string[]) => void
  /** 「补充说明」：把焦点交回输入框，让用户用自己的话补充。 */
  onSupplement: () => void
  /** PATCH 进行中（避免连点）。 */
  disabled?: boolean
  /** 思考过程（与提问同属**一条**助手消息，不单独占一块）。 */
  trace?: ReactNode
  /**
   * 只出内容（2026-11-05 修订 A4）——外壳（头像 + 气泡）由 `AssistantTurn` 提供，
   * 也就是「一轮只有一条助手消息」。默认形态保持不变（历史/单测直接用）。
   */
  bare?: boolean
}

/**
 * 需求澄清提问（2026-11-04，plan chat-like-conversation-flow）。
 *
 * **形态：普通助手消息**，不是一张表。用户明确说过「不要卡片的方式、这就是在对话」——
 * 之前这里是个带 kicker「需要你定口径」的边框面板，读起来像表单。现在：头像 + 气泡 +
 * 气泡内的可点选项，与 `AgentBubble` 同一套视觉，答完直接出下一个消息。
 */
export default function QuestionBar({
  field,
  onPick,
  onSupplement,
  disabled = false,
  trace = null,
  bare = false,
}: Props) {
  const multiple = field.kind === 'multiple'
  const lead =
    field.state === 'ambiguous' ? '先确认一下口径：' : '还需要你补充一点：'

  const content = (
    <>
      <p className="wb-ask-text">
        {lead}
        {field.label}
      </p>
      <div className="wb-ask-options">
        {field.options.map((option) => (
          <button
            key={option.value}
            type="button"
            className="wb-ask-option"
            onClick={() => {
              if (!disabled) onPick(field.key, option.value)
            }}
            disabled={disabled}
            title={option.value}
          >
            <span className="wb-ask-option-label">{option.label}</span>
            {option.hint && <span className="wb-ask-option-hint">{option.hint}</span>}
          </button>
        ))}
      </div>
      <div className="wb-ask-foot">
        <button type="button" className="wb-ask-link" onClick={onSupplement}>
          都不是，我补充说明
        </button>
        {multiple && <span className="wb-ask-note">可多选</span>}
      </div>
    </>
  )

  if (bare) {
    return (
      <div className="wb-turn-body" role="group" aria-label="需求澄清">
        {content}
      </div>
    )
  }

  return (
    <div className="wb-message wb-reveal" role="group" aria-label="需求澄清">
      <div className="wb-msg-avatar agent">A</div>
      <div className="wb-bubble wb-ask-bubble">
        {trace}
        {content}
      </div>
    </div>
  )
}
