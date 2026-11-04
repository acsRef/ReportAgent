import type { RequirementMissingField } from '../../types/requirement'

interface Props {
  field: RequirementMissingField
  /** 选中某个选项（服务端 PATCH 后由新卡推导下一个问题）。 */
  onPick: (key: string, value: string | string[]) => void
  /** 「补充说明」：把焦点交回输入框，让用户用自己的话补充。 */
  onSupplement: () => void
  /** PATCH 进行中（避免连点）。 */
  disabled?: boolean
}

/**
 * 需求澄清的「一次只问一个」提问条（2026-11-04，plan 2026-11-04-requirement-slot-contract Phase 5）。
 *
 * 为什么不再一次性铺开整张卡：整卡把 5 组 chip + 缺失区 + 假设区一次摊开，像填表而不像
 * 对话，用户不知道先答哪个。这里一条气泡只问**当前最该问的那一个**槽位，
 * 选项带口径预览数值（`hint`），答完立刻出下一问；完整卡降级为「展开详情」。
 */
export default function QuestionBar({ field, onPick, onSupplement, disabled = false }: Props) {
  const multiple = field.kind === 'multiple'

  function handlePick(value: string) {
    if (disabled) return
    onPick(field.key, value)
  }

  return (
    <div className="wb-question-bar wb-reveal" role="group" aria-label="需求澄清">
      <div className="wb-question-head">
        <span className="wb-question-kicker">
          {field.state === 'ambiguous' ? '需要你定口径' : '还需要一个信息'}
        </span>
        <span className="wb-question-label">{field.label}</span>
      </div>

      <div className="wb-question-options">
        {field.options.map((option) => (
          <button
            key={option.value}
            type="button"
            className="wb-question-option"
            onClick={() => handlePick(option.value)}
            disabled={disabled}
            title={option.value}
          >
            <span className="wb-question-option-label">{option.label}</span>
            {option.hint && <span className="wb-question-option-hint">{option.hint}</span>}
          </button>
        ))}
      </div>

      <div className="wb-question-foot">
        <button type="button" className="wb-question-supplement" onClick={onSupplement}>
          都不是，我补充说明
        </button>
        {multiple && (
          <span className="wb-question-note">可多选：先点一个，稍后可在详情里继续加</span>
        )}
      </div>
    </div>
  )
}
