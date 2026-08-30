import { ADJUST_STAGES, CONFIRM_STAGES, progressPercent, stagePrefix } from './progressModel'

interface Props {
  adjusting?: boolean
  /** 0-based index of the active stage; >= stages.length means all done. */
  stageIndex: number
  failed?: boolean
  /** P11：live 当前步骤文案（trace 驱动），覆盖默认 detail 行。 */
  liveDetail?: string
  onStop?: () => void
}

/** generating/adjusting progress card — prototype .progress-card. */
export default function ProgressCard({ adjusting, stageIndex, failed, liveDetail, onStop }: Props) {
  const stages = adjusting ? ADJUST_STAGES : CONFIRM_STAGES
  const percent = stageIndex >= stages.length ? 100 : progressPercent(stageIndex)
  const activeLabel = stages[Math.min(stageIndex, stages.length - 1)]

  return (
    <div className={`wb-progress-card wb-reveal${failed ? ' failed' : ''}`}>
      <div className="wb-progress-top">
        <span className="wb-progress-title">
          {failed ? '执行分析时发生错误' : adjusting ? '正在生成新报告版本' : 'Agent 正在执行分析'}
        </span>
        <span className="wb-progress-pct">{failed ? 'FAILED' : `${percent}%`}</span>
      </div>

      <div className="wb-progress-track">
        <div className="wb-progress-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="wb-stage-list">
        {stages.map((label, index) => {
          const stateClass =
            index < stageIndex ? 'wb-stage done' : index === stageIndex && !failed ? 'wb-stage active' : 'wb-stage'
          return (
            <div key={label} className={stateClass}>
              {stagePrefix(index, stageIndex)}
              {label}
            </div>
          )
        })}
      </div>

      <div className="wb-progress-detail">
        {liveDetail
          ? liveDetail
          : failed
            ? '可以重试当前任务，已确认需求保持不变。'
            : adjusting
              ? '原报告仍可回看'
              : `${activeLabel} · 确认后才开始正式查询`}
      </div>

      {!failed && onStop && (
        <div className="wb-progress-actions">
          <button type="button" className="wb-danger-btn" onClick={onStop}>
            停止生成
          </button>
        </div>
      )}
    </div>
  )
}
