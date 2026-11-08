import { useState, type ReactNode } from 'react'
import type { RequirementCard } from '../../types/requirement'
import RequirementCardView from './RequirementCardView'
import { describeRequirement } from './slotQuestion'

interface Props {
  card: RequirementCard
  onConfirm: () => void
  /** 「补充或修改」：把焦点交回输入框，用户用自己的话改（走 supplement 重解析）。 */
  onSupplement?: () => void
  /** PATCH/确认进行中。 */
  busy?: boolean
  /** 思考过程（与这句话同属**一条**助手消息）。 */
  trace?: ReactNode
  /**
   * 只出内容（2026-11-05 修订 A4）——外壳（头像 + 气泡）由 `AssistantTurn` 提供。
   * 默认形态保持不变（历史/单测直接用）。
   */
  bare?: boolean
}

/**
 * 需求确认 —— **就是一句话**（2026-11-05 第二次改造）。
 *
 * 用户原话：「不要卡片的话，直接改成对话的方式会不会好一些，现在这个样子太奇怪了。
 * 直接从对话中来读取意图」。
 *
 * 形态：
 *
 *   A  ▸ 思考了 6 步 · 7.7s
 *      好，2024年华东的销售额，按月看。要我现在生成报告吗？
 *      [ 生成报告 ]   补充或修改   需求详情
 *
 * 参数就是那句话本身；要改直接说一句（或点「补充或修改」把光标放进输入框）。
 * 「需求详情」是**按需**视图（plan conversation-transcript-messages 修订 A4 的
 * 「卡片只作详情」）：点开在上方就地展开，确认按钮原地不动，不再自动出现整卡。
 */
export default function ConfirmBar({
  card,
  onConfirm,
  onSupplement,
  busy = false,
  trace = null,
  bare = false,
}: Props) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const description = describeRequirement(card)
  // 2026-11-08（真机）：卡还缺项时，这句「要我现在生成报告吗？」是假话——
  // 用户看到「待确认 1 项（指标未注册：销售）」却还被告知可以生成。
  // 缺项就把话说清楚，并禁用主按钮（后端 gate 本来也会拦）。
  const pending = card.missing_fields ?? []
  const blocked = pending.length > 0
  const pendingText = pending
    .map((field) => field.label)
    .filter(Boolean)
    .join('、')

  const content = (
    <>
      {blocked ? (
        <p className="wb-ask-text">
          这里还差 {pending.length} 项，先补上才能生成：
          <strong>{pendingText || '需求信息'}</strong>
          。你可以直接说一句，或点下面的「补充或修改」。
        </p>
      ) : (
        <p className="wb-ask-text">好，{description}。要我现在生成报告吗？</p>
      )}
      {detailsOpen && (
        <div className="wb-ask-details">
          <RequirementCardView
            card={card}
            variant="inline"
            // inline 形态没有编辑区（缺失项/底部动作都不渲染），onChange 不会被调用。
            onChange={() => undefined}
            onConfirm={onConfirm}
          />
        </div>
      )}
      <div className="wb-ask-foot">
        <button
          type="button"
          className="wb-primary wb-ask-confirm"
          onClick={onConfirm}
          disabled={busy || blocked}
          title={blocked ? '先补上缺的项才能生成报告' : undefined}
        >
          生成报告
        </button>
        {onSupplement && (
          <button
            type="button"
            className="wb-ask-link"
            onClick={onSupplement}
            disabled={busy}
            title="直接说哪里不对，例如「按日看」「再加上华南」"
          >
            补充或修改
          </button>
        )}
        <button
          type="button"
          className="wb-ask-link"
          onClick={() => setDetailsOpen((open) => !open)}
          aria-expanded={detailsOpen}
        >
          {detailsOpen ? '收起详情' : '需求详情'}
        </button>
      </div>
    </>
  )

  if (bare) {
    return (
      <div className="wb-turn-body" role="group" aria-label="需求确认">
        {content}
      </div>
    )
  }

  return (
    <div className="wb-message wb-reveal" role="group" aria-label="需求确认">
      <div className="wb-msg-avatar agent">A</div>
      <div className="wb-bubble wb-ask-bubble">
        {trace}
        {content}
      </div>
    </div>
  )
}
