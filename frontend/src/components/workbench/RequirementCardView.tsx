/**
 * RequirementCardView — render a RequirementCard with form controls.
 *
 * Layout:
 *   - 标题 + status 标签
 *   - 每个 missing_field 一个 segmented / checkbox 组（按 kind）
 *   - 每个 assumption 一行 [✓ ✗ 文字]
 *   - 底部 [确认执行] 按钮（仅 status=complete 时启用）
 *
 * Mutations are pushed up via the `onChange` callback; the parent owns
 * the source of truth (analysisStore).
 */
import { App, Button, Checkbox, Radio, Space, Tag, Typography } from 'antd'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import type { RequirementCard as RC } from '../../types/requirement'

interface Props {
  card: RC
  onChange: (next: RC) => void
  onConfirm: () => void
  confirming?: boolean
}

const STATUS_COLOR: Record<RC['status'], string> = {
  missing: 'amber',
  complete: 'teal',
  locked: 'ink',
}

export default function RequirementCardView({ card, onChange, onConfirm, confirming }: Props) {
  const { message } = App.useApp()
  const canConfirm = card.status === 'complete'

  function setSelectedValue(key: string, value: string | string[] | null) {
    onChange({
      ...card,
      missing_fields: card.missing_fields.map((mf) =>
        mf.key === key ? { ...mf, selected_value: value } : mf,
      ),
    })
  }

  function setAssumptionAccepted(key: string, accepted: boolean) {
    onChange({
      ...card,
      assumptions: card.assumptions.map((a) =>
        a.key === key ? { ...a, accepted } : a,
      ),
    })
  }

  async function handleConfirm() {
    // Sanity: at least one missing field selected?
    const unfilled = card.missing_fields.filter((mf) => {
      const v = mf.selected_value
      return v === null || v === '' || (Array.isArray(v) && v.length === 0)
    })
    if (unfilled.length > 0) {
      message.warning(`还有 ${unfilled.length} 个字段未选择`)
      return
    }
    const unresolved = card.assumptions.filter((a) => a.accepted === null)
    if (unresolved.length > 0) {
      message.warning(`还有 ${unresolved.length} 个待确认假设未表态`)
      return
    }
    onConfirm()
  }

  return (
    <div
      style={{
        background: 'var(--paper)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-m)',
        padding: 'var(--sp-l)',
        boxShadow: 'var(--shadow-soft)',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <Typography.Title
          level={4}
          style={{
            fontFamily: 'var(--font-display)',
            color: 'var(--ink)',
            margin: 0,
            fontSize: 18,
          }}
        >
          需求卡
        </Typography.Title>
        <Tag color={STATUS_COLOR[card.status]} style={{ fontWeight: 600 }}>
          {card.status.toUpperCase()}
        </Tag>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--muted)' }}>
          置信度 {(card.confidence * 100).toFixed(0)}%
        </span>
      </div>

      <Typography.Paragraph
        style={{ color: 'var(--ink-2)', fontSize: 13, marginBottom: 18, fontFamily: 'var(--font-display)' }}
      >
        {card.summary}
      </Typography.Paragraph>

      {/* Missing fields */}
      {card.missing_fields.length > 0 && (
        <>
          <SectionTitle>待补充 ({card.missing_fields.length})</SectionTitle>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            {card.missing_fields.map((mf) => (
              <div key={mf.key}>
                <div
                  style={{
                    fontSize: 11,
                    letterSpacing: 1.2,
                    color: 'var(--muted)',
                    textTransform: 'uppercase',
                    fontWeight: 700,
                    marginBottom: 6,
                  }}
                >
                  {mf.label}
                </div>
                {mf.kind === 'single' ? (
                  <Radio.Group
                    value={mf.selected_value as string | undefined}
                    onChange={(e) => setSelectedValue(mf.key, e.target.value)}
                    style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}
                  >
                    {mf.options.map((o) => (
                      <Radio.Button key={o.value} value={o.value}>
                        {o.label}
                      </Radio.Button>
                    ))}
                  </Radio.Group>
                ) : (
                  <Checkbox.Group
                    value={(mf.selected_value as string[] | undefined) ?? []}
                    onChange={(vals) => setSelectedValue(mf.key, vals as string[])}
                    style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}
                  >
                    {mf.options.map((o) => (
                      <Checkbox key={o.value} value={o.value}>
                        {o.label}
                      </Checkbox>
                    ))}
                  </Checkbox.Group>
                )}
              </div>
            ))}
          </Space>
        </>
      )}

      {/* Assumptions */}
      {card.assumptions.length > 0 && (
        <>
          <SectionTitle>待确认假设 ({card.assumptions.length})</SectionTitle>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {card.assumptions.map((a) => (
              <div
                key={a.key}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 10,
                  padding: '8px 10px',
                  background: a.accepted === true ? 'var(--teal-pale)' : a.accepted === false ? 'var(--red-soft)' : 'var(--canvas)',
                  border: '1px solid var(--line)',
                  borderRadius: 6,
                }}
              >
                <Button
                  size="small"
                  type={a.accepted === true ? 'primary' : 'default'}
                  icon={<CheckOutlined />}
                  onClick={() => setAssumptionAccepted(a.key, true)}
                />
                <Button
                  size="small"
                  danger
                  type={a.accepted === false ? 'primary' : 'default'}
                  icon={<CloseOutlined />}
                  onClick={() => setAssumptionAccepted(a.key, false)}
                />
                <span style={{ flex: 1, color: 'var(--ink-2)', fontSize: 12 }}>{a.text}</span>
              </div>
            ))}
          </Space>
        </>
      )}

      {/* Confirm button */}
      <div style={{ marginTop: 18, display: 'flex', justifyContent: 'flex-end' }}>
        <Button
          type="primary"
          disabled={!canConfirm}
          loading={confirming}
          onClick={handleConfirm}
        >
          确认执行
        </Button>
      </div>
    </div>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 10,
        letterSpacing: 1.4,
        color: 'var(--muted)',
        textTransform: 'uppercase',
        fontWeight: 700,
        margin: '14px 0 8px',
        borderTop: '1px solid var(--line)',
        paddingTop: 10,
      }}
    >
      {children}
    </div>
  )
}
