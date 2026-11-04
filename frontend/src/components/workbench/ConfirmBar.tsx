import type { RequirementCard } from '../../types/requirement'
import { confirmChips } from './slotQuestion'

interface Props {
  card: RequirementCard
  /** 补充轮带来的变化说明（复用 WorkbenchPage 的 changeNote）。 */
  changeNote?: string | null
  onConfirm: () => void
  onExpand: () => void
  /** PATCH/确认进行中。 */
  busy?: boolean
}

/**
 * 紧凑确认条（2026-11-04，plan 2026-11-04-requirement-slot-contract Phase 5）。
 *
 * 槽位全部问完之后，不再甩出整张卡，而是一行已确认摘要 + 「确认并生成报告」：
 * 用户看到的是「我确认的是什么」，而不是又一张表单。完整卡仍可「展开详情」。
 */
export default function ConfirmBar({
  card,
  changeNote = null,
  onConfirm,
  onExpand,
  busy = false,
}: Props) {
  const chips = confirmChips(card)

  return (
    <div className="wb-confirm-bar wb-reveal" role="group" aria-label="需求确认条">
      <div className="wb-confirm-main">
        <span className="wb-confirm-kicker">需求已明确</span>
        <div className="wb-confirm-chips">
          {chips.length === 0 ? (
            <span className="wb-confirm-empty">按你的问题直接分析</span>
          ) : (
            chips.map((chip) => (
              <span key={chip.key} className="wb-confirm-chip">
                {chip.text}
              </span>
            ))
          )}
        </div>
        {changeNote && <div className="wb-confirm-change">↻ {changeNote}</div>}
      </div>

      <div className="wb-confirm-actions">
        <button type="button" className="wb-quiet-btn" onClick={onExpand}>
          展开详情
        </button>
        <button type="button" className="wb-primary" onClick={onConfirm} disabled={busy}>
          确认并生成报告
        </button>
      </div>
    </div>
  )
}
