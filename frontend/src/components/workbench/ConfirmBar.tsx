import type { ReactNode } from 'react'
import type { RequirementCard } from '../../types/requirement'
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
}

/**
 * 需求确认 —— **就是一句话**（2026-11-05 第二次改造）。
 *
 * 用户原话：「不要卡片的话，直接改成对话的方式会不会好一些，现在这个样子太奇怪了。
 * 直接从对话中来读取意图」。
 *
 * 之前这里还有「已按你的补充更新：维度 → 时间、区域」这种 diff + 一排参数 chip +
 * 「需求详情」，读起来像表单回执。现在只剩：
 *
 *   A  ▸ 思考了 6 步 · 7.7s
 *      好，2024年华东的销售额，按月看。要我现在生成报告吗？
 *      [ 生成报告 ]   补充或修改
 *
 * 参数就是那句话本身；要改直接说一句（或点「补充或修改」把光标放进输入框）。
 */
export default function ConfirmBar({
  card,
  onConfirm,
  onSupplement,
  busy = false,
  trace = null,
}: Props) {
  const description = describeRequirement(card)

  return (
    <div className="wb-message wb-reveal" role="group" aria-label="需求确认">
      <div className="wb-msg-avatar agent">A</div>
      <div className="wb-bubble wb-ask-bubble">
        {trace}
        <p className="wb-ask-text">
          好，{description}。要我现在生成报告吗？
        </p>
        <div className="wb-ask-foot">
          <button
            type="button"
            className="wb-primary wb-ask-confirm"
            onClick={onConfirm}
            disabled={busy}
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
        </div>
      </div>
    </div>
  )
}
