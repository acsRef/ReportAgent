import RadioGroup from '../atelier/RadioGroup'
import CheckboxGroup from '../atelier/CheckboxGroup'
import { methodLabel } from './slotQuestion'
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
  /** 已确认并开始执行后置 true：卡片只读，改需求走输入框（2026-11-02）。 */
  readOnly?: boolean
  /** 补充轮带来的变化说明（2026-11-03）：让用户看到「同一张卡被更新」，而不是又冒出一张新卡。 */
  changeNote?: string | null
  /**
   * 呈现形态（2026-11-04，plan requirement-details-inline）。
   *
   * - `full`：完整 brief（kicker + 标题 + 状态 pill + 缺失项 + 底部动作）——执行中只读时用；
   * - `inline`：**折进助手消息里的详情**——没有卡片外壳/标题/底部按钮，只有
   *   一句需求摘要 + 参数网格。点「需求详情」展开的就是它，不再是屏幕中间另起一张大卡。
   */
  variant?: 'full' | 'inline'
}

const STATUS_PILL: Record<RC['status'], string> = {
  missing: '', // computed with count
  complete: '信息完整 · 待确认',
  locked: '✓ 已确认',
}

/**
 * 粒度中文标签（2026-11-03，plan 2026-11-03-anomaly-granularity）。
 *
 * 之前粒度只活在卡体字段里、界面上完全没有——用户看不到「异常类默认按周」，
 * 也不知道结果为什么是 53 行而不是 12 行。按周/按日是这个产品最常被追问的口径。
 */
const GRANULARITY_LABELS: Record<NonNullable<RC['granularity']>, string> = {
  day: '按日',
  week: '按周',
  month: '按月',
  quarter: '按季度',
  year: '按年',
}

/**
 * Requirement card per docs/intelligent-analysis-workbench.html:
 * left accent bar by status, kicker + status pill head, serif summary,
 * chips grid, missing-zone with option pills + assumption bar, footer
 * actions. No spinner anywhere — busy states live in the composer and
 * the progress card (WorkbenchPage).
 */
export default function RequirementCardView({ card, onChange, onConfirm, onFocusComposer, readOnly = false, changeNote = null, variant = 'full' }: Props) {
  const ready = isDraftReadyForReview(card)
  const inline = variant === 'inline'

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

  const pendingAssumptions = card.assumptions.filter((a) => a.accepted === null).length
  // 2026-11-04：status=missing 但 missing_fields 为空时，之前显示「需要补充 0 项」——自相矛盾。
  // 真正待办是「还没表态的假设」，如实说。
  const pill =
    card.status === 'missing'
      ? card.missing_fields.length > 0
        ? `需要补充 ${card.missing_fields.length} 项`
        : pendingAssumptions > 0
          ? `待确认 ${pendingAssumptions} 项假设`
          : '待确认'
      : STATUS_PILL[card.status]

  return (
    <div className={`wb-requirement-card ${inline ? 'inline' : ''} ${card.status} wb-reveal`}>
      {!inline && (
        <div className="wb-req-head">
          <div>
            <div className="wb-req-kicker">AGENT REQUIREMENT BRIEF</div>
            <h3 className="wb-req-title">需求解析与执行确认</h3>
          </div>
          <span className="wb-req-status">{pill}</span>
        </div>
      )}

      {inline && (
        <div className="wb-inline-head">
          <span className="wb-inline-label">需求详情</span>
          <span className="wb-req-status">{pill}</span>
        </div>
      )}

      <div className={inline ? 'wb-req-summary inline' : 'wb-req-summary'}>{card.summary}</div>

      {changeNote && (
        <div className="wb-req-changenote">↻ 已按你的补充更新：{changeNote}</div>
      )}

      <div className={inline ? 'wb-req-body inline' : 'wb-req-body'}>
        <div className="wb-req-grid">
          {card.target_metrics.length > 0 && (
            <div>
              <div className="wb-req-label">核心指标</div>
              <div className="wb-chips">
                {card.target_metrics.map((metric) => (
                  <span key={metric} className="wb-chip">{metric}</span>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="wb-req-label">时间与范围</div>
            <div className="wb-chips">
              <span className="wb-chip">{card.time_range ?? '时间待补充'}</span>
              {card.granularity && (
                <span className="wb-chip granularity" title="时间聚合粒度">
                  {GRANULARITY_LABELS[card.granularity] ?? card.granularity}
                </span>
              )}
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
          {card.dimensions.length > 0 && (
            <div>
              <div className="wb-req-label">分析维度</div>
              <div className="wb-chips">
                {card.dimensions.map((dimension) => (
                  <span key={dimension} className="wb-chip">{dimension}</span>
                ))}
              </div>
            </div>
          )}
          {card.analysis_methods.length > 0 && (
            <div>
              <div className="wb-req-label">建议分析方法</div>
              <div className="wb-chips">
                {card.analysis_methods.map((method) => (
                  <span key={method} className="wb-chip method">{methodLabel(method)}</span>
                ))}
              </div>
            </div>
          )}
          {/* 空分组不再画一个只有标题的空壳（截图里「预计报告内容」下面一片空白，很丑） */}
          {card.expected_blocks.length > 0 && (
            <div style={{ gridColumn: '1 / -1' }}>
              <div className="wb-req-label">预计报告内容</div>
              <div className="wb-chips">
                {card.expected_blocks.map((block) => (
                  <span key={block} className="wb-chip block">{block}</span>
                ))}
              </div>
            </div>
          )}
        </div>

        {!inline && !readOnly && card.status === 'missing' && (card.missing_fields.length > 0 || card.assumptions.length > 0) && (
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

      {/* inline 详情没有自己的底部动作：确认按钮就在它上面那条消息的 foot 里，
          两个「确认并生成报告」并排只会让人不知道该点哪个。 */}
      {inline ? null : readOnly ? (
        <div className="wb-req-actions">
          <span className="wb-req-hint">需求已确认并锁定，报表生成中；要改需求请在下方输入框说明</span>
        </div>
      ) : card.status !== 'locked' && (
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
