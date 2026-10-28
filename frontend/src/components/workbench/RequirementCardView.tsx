import RadioGroup from '../atelier/RadioGroup'
import CheckboxGroup from '../atelier/CheckboxGroup'
import {
  isDraftReadyForReview,
  type RequirementAssumption,
  type RequirementCard as RC,
  type RequirementOption,
} from '../../types/requirement'

interface Props {
  card: RC
  onChange: (next: RC) => void
  onConfirm: () => void
  /** Focuses the composer (prototype: 继续对话补充). */
  onFocusComposer?: () => void
}

const STATUS_PILL: Record<RC['status'], string> = {
  missing: '', // computed with count
  complete: '信息完整 · 待确认',
  locked: '✓ 已确认',
}

/**
 * Requirement card per docs/intelligent-analysis-workbench.html:
 * left accent bar by status, kicker + status pill head, serif summary,
 * chips grid, missing-zone with option pills + assumption bar, footer
 * actions. No spinner anywhere — busy states live in the composer and
 * the progress card (WorkbenchPage).
 */
export default function RequirementCardView({ card, onChange, onConfirm, onFocusComposer }: Props) {
  const ready = isDraftReadyForReview(card)

  function setSelectedValue(key: string, value: string | string[] | null) {
    onChange({
      ...card,
      missing_fields: card.missing_fields.map((field) =>
        field.key === key ? { ...field, selected_value: value } : field,
      ),
    })
  }

  function setAssumptionAccepted(key: string, accepted: boolean) {
    onChange({
      ...card,
      assumptions: card.assumptions.map((assumption) =>
        assumption.key === key ? { ...assumption, accepted } : assumption,
      ),
    })
  }

  /**
   * 一键定口径：点候选项 = 接受该假设 + 把口径写进假设正文。
   *
   * 为什么不新增字段：`_format_confirmed_requirement` 只把「已接受假设的 text」拼进
   * SQL plan prompt——写进 text 才真正到达下游；新增字段要同时改后端模型、格式化器、
   * PII/安全闸与镜像测试，为一个 UI 动作扩大契约面不值得。
   * 重复点击先剥掉旧的「→ 已选：...」，避免后缀叠加。
   */
  function chooseAssumptionAlternative(
    assumption: RequirementAssumption,
    option: RequirementOption,
  ) {
    const baseText = assumption.text.replace(/\s*→\s*已选：.*$/, '')
    onChange({
      ...card,
      assumptions: card.assumptions.map((item) =>
        item.key === assumption.key
          ? { ...item, accepted: true, text: `${baseText} → 已选：${option.label}` }
          : item,
      ),
    })
  }

  function handleReview() {
    onChange({ ...card, status: 'complete', confirmed_at: null })
  }

  function handleModify() {
    onChange({ ...card, status: 'missing', confirmed_at: null })
  }

  const pill = card.status === 'missing' ? `需要补充 ${card.missing_fields.length} 项` : STATUS_PILL[card.status]

  return (
    <div className={`wb-requirement-card ${card.status} wb-reveal`}>
      <div className="wb-req-head">
        <div>
          <div className="wb-req-kicker">AGENT REQUIREMENT BRIEF</div>
          <h3 className="wb-req-title">需求解析与执行确认</h3>
        </div>
        <span className="wb-req-status">{pill}</span>
      </div>

      <div className="wb-req-summary">{card.summary}</div>

      <div className="wb-req-body">
        <div className="wb-req-grid">
          <div>
            <div className="wb-req-label">核心指标</div>
            <div className="wb-chips">
              {card.target_metrics.map((metric) => (
                <span key={metric} className="wb-chip">{metric}</span>
              ))}
            </div>
          </div>
          <div>
            <div className="wb-req-label">时间与范围</div>
            <div className="wb-chips">
              <span className="wb-chip">{card.time_range ?? '时间待补充'}</span>
              {card.scope.length > 0
                ? card.scope.map((item) => <span key={item} className="wb-chip">{item}</span>)
                // 后端契约：scope 为空 = 未限定（默认 ALL），不是缺失。只有真的出现在
                // missing_fields 里才是「待补充」——否则会把「全国/全品类」误读成让你填。
                : (
                  <span className="wb-chip">
                    {card.missing_fields.some((field) => field.key === 'scope')
                      ? '范围待补充'
                      : '未限定（全部区域）'}
                  </span>
                )}
            </div>
          </div>
          <div>
            <div className="wb-req-label">分析维度</div>
            <div className="wb-chips">
              {card.dimensions.map((dimension) => (
                <span key={dimension} className="wb-chip">{dimension}</span>
              ))}
            </div>
          </div>
          <div>
            <div className="wb-req-label">建议分析方法</div>
            <div className="wb-chips">
              {card.analysis_methods.map((method) => (
                <span key={method} className="wb-chip method">{method}</span>
              ))}
            </div>
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <div className="wb-req-label">预计报告内容</div>
            <div className="wb-chips">
              {card.expected_blocks.map((block) => (
                <span key={block} className="wb-chip block">{block}</span>
              ))}
            </div>
          </div>
        </div>

        {card.status === 'missing' && (card.missing_fields.length > 0 || card.assumptions.length > 0) && (
          <div className="wb-missing-zone">
            <div className="wb-missing-heading">
              <span className="wb-missing-title">需要你确认的信息</span>
              <span className="wb-missing-note">选项由后端根据当前问题返回</span>
            </div>

            {card.missing_fields.map((field) => (
              <div key={field.key} className="wb-option-group">
                <div className="wb-option-label">{field.label}</div>
                {field.kind === 'single' ? (
                  <RadioGroup
                    kind="pill"
                    options={field.options.map((option) => ({ value: option.value, label: option.label }))}
                    value={field.selected_value as string | null}
                    onChange={(value) => setSelectedValue(field.key, value)}
                  />
                ) : (
                  <CheckboxGroup
                    options={field.options.map((option) => ({ value: option.value, label: option.label }))}
                    value={(field.selected_value as string[] | undefined) ?? []}
                    onChange={(values) => setSelectedValue(field.key, values)}
                  />
                )}
              </div>
            ))}

            {card.assumptions.map((assumption) => (
              <div key={assumption.key} className="wb-assumption" style={{ marginBottom: 6 }}>
                <div>
                  <span>Agent 暂时假设：{assumption.text}</span>
                  {assumption.alternatives.length > 0 && assumption.accepted !== true && (
                    <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {assumption.alternatives.map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          className="wb-chip method"
                          title={option.value}
                          onClick={() => chooseAssumptionAlternative(assumption, option)}
                        >
                          选：{option.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <span className="wb-assumption-actions">
                  <button
                    type="button"
                    className={assumption.accepted === true ? 'wb-mini-btn accepted' : 'wb-mini-btn'}
                    onClick={() => setAssumptionAccepted(assumption.key, true)}
                  >
                    {assumption.accepted === true ? '✓ 已接受' : '接受'}
                  </button>
                  {assumption.accepted !== true && (
                    <button
                      type="button"
                      className="wb-mini-btn"
                      onClick={() => setAssumptionAccepted(assumption.key, false)}
                    >
                      修改
                    </button>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {card.status !== 'locked' && (
        <div className="wb-req-actions">
          <span className="wb-req-hint">确认后 Agent 才会查询数据并生成报告</span>
          <span className="wb-action-group">
            {card.status === 'missing' ? (
              <>
                <button type="button" className="wb-secondary" onClick={onFocusComposer}>
                  继续对话补充
                </button>
                <button type="button" className="wb-primary" disabled={!ready} onClick={handleReview}>
                  补充完成，查看确认
                </button>
              </>
            ) : (
              <>
                <button type="button" className="wb-secondary" onClick={handleModify}>
                  修改需求
                </button>
                <button type="button" className="wb-primary" onClick={onConfirm}>
                  确认并生成报告
                </button>
              </>
            )}
          </span>
        </div>
      )}
    </div>
  )
}
