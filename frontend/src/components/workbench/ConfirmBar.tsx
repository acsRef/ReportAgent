import type { ReactNode } from 'react'
import type { RequirementCard } from '../../types/requirement'
import { confirmChips } from './slotQuestion'

interface Props {
  card: RequirementCard
  /** 补充轮带来的变化说明。 */
  changeNote?: string | null
  onConfirm: () => void
  /**
   * 「补充或修改」：把焦点交回输入框，用户用自己的话改（走 supplement 重解析）。
   *
   * 2026-11-04 用户质问：「难道我就只能确认并生成报告吗 没有补充吗？」——
   * 之前确认条只摆了一个主按钮 + 一个「需求详情」，看上去像「要么签要么看」，
   * 而「直接说一句补充」这条**真实存在**的路子完全不可见（只能靠猜输入框）。
   */
  onSupplement?: () => void
  /** 展开 / 收起内联详情（同一个开关，文案随之变化）。 */
  onToggleDetails: () => void
  /** 详情是否已展开。 */
  expanded?: boolean
  /** PATCH/确认进行中。 */
  busy?: boolean
  /** 思考过程（与确认同属**一条**助手消息）。 */
  trace?: ReactNode
  /** 展开后的详情内容（内联在这条消息里，不是另起一张大卡）。 */
  details?: ReactNode
}

/**
 * 需求确认（2026-11-04，plan chat-like-conversation-flow + requirement-details-inline）。
 *
 * **形态：一条普通助手消息**，不是确认单。之前是带 kicker「需求已明确」的边框条，
 * 用户原话「不要用卡片的方式、这就是在对话」。现在：头像 + 一句话说明 + 参数小结 +
 * 一个「确认并生成」按钮；想改需求直接在下条消息里说。
 *
 * 点「需求详情」不换页面、也不另起一张大卡：详情**就地展开在这条气泡里**，
 * 链接变成「收起详情」，确认按钮仍然在原来的位置——用户不会因为「展开」而
 * 丢掉那个唯一的动作。
 */
export default function ConfirmBar({
  card,
  changeNote = null,
  onConfirm,
  onSupplement,
  onToggleDetails,
  expanded = false,
  busy = false,
  trace = null,
  details = null,
}: Props) {
  const chips = confirmChips(card)

  return (
    <div className="wb-message wb-reveal" role="group" aria-label="需求确认">
      <div className="wb-msg-avatar agent">A</div>
      <div className="wb-bubble wb-ask-bubble">
        {trace}
        <p className="wb-ask-text">
          {changeNote ? `已按你的补充更新：${changeNote}` : '需求清楚了，我按下面这些执行：'}
        </p>
        {chips.length > 0 && (
          <div className="wb-ask-chips">
            {chips.map((chip) => (
              <span key={chip.key} className="wb-ask-chip">
                {chip.text}
              </span>
            ))}
          </div>
        )}
        <div className="wb-ask-foot">
          <button
            type="button"
            className="wb-primary wb-ask-confirm"
            onClick={onConfirm}
            disabled={busy}
          >
            确认并生成报告
          </button>
          {/* 第二个动作必须是「还能改」，否则这一屏读起来像只能签字 */}
          {onSupplement && (
            <button
              type="button"
              className="wb-ask-secondary"
              onClick={onSupplement}
              disabled={busy}
              title="直接在下面输入，例如「按日看」「再加上华南」"
            >
              补充或修改
            </button>
          )}
          <button
            type="button"
            className="wb-ask-link"
            onClick={onToggleDetails}
            aria-expanded={expanded}
          >
            {expanded ? '收起详情 ↑' : '需求详情'}
          </button>
        </div>
        {expanded && details && <div className="wb-ask-details">{details}</div>}
      </div>
    </div>
  )
}
