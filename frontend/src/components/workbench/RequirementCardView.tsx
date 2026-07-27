import { Title, Paragraph } from '../atelier/Typography'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import Button from '../atelier/Button'
import Tag from '../atelier/Tag'
import RadioGroup from '../atelier/RadioGroup'
import CheckboxGroup from '../atelier/CheckboxGroup'
import { useToast } from '../atelier/useToast'
import type { RequirementCard as RC } from '../../types/requirement'

interface Props {
  card: RC
  onChange: (next: RC) => void
  onConfirm: () => void
  confirming?: boolean
}

const STATUS_COLOR: Record<RC['status'], 'amber' | 'teal' | 'ink'> = {
  missing: 'amber',
  complete: 'teal',
  locked: 'ink',
}

export default function RequirementCardView({ card, onChange, onConfirm, confirming }: Props) {
  const toast = useToast()

  const unfilled = card.missing_fields.filter((mf) => {
    const v = mf.selected_value
    return v === null || v === '' || (Array.isArray(v) && v.length === 0)
  })
  const unresolved = card.assumptions.filter((a) => a.accepted === null)
  const canConfirm = unfilled.length === 0 && unresolved.length === 0

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
    if (unfilled.length > 0) {
      toast.warning(`还有 ${unfilled.length} 个字段未选择`)
      return
    }
    if (unresolved.length > 0) {
      toast.warning(`还有 ${unresolved.length} 个待确认假设未表态`)
      return
    }
    const promoted: RC = {
      ...card,
      status: 'complete',
      confirmed_at: null,
    }
    onChange(promoted)
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <Title
          level={4}
          style={{
            fontFamily: 'var(--font-display)',
            color: 'var(--ink)',
            margin: 0,
            fontSize: 18,
          }}
        >
          需求卡
        </Title>
        <Tag tone={STATUS_COLOR[card.status]} style={{ fontWeight: 600 }}>
          {card.status.toUpperCase()}
        </Tag>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--muted)' }}>
          置信度 {(card.confidence * 100).toFixed(0)}%
        </span>
      </div>

      <Paragraph
        style={{ color: 'var(--ink-2)', fontSize: 13, marginBottom: 18, fontFamily: 'var(--font-display)' }}
      >
        {card.summary}
      </Paragraph>

      {card.missing_fields.length > 0 && (
        <>
          <SectionTitle>待补充 ({card.missing_fields.length})</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, width: '100%' }}>
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
                  <RadioGroup
                    kind="pill"
                    options={mf.options.map((o) => ({ value: o.value, label: o.label }))}
                    value={mf.selected_value as string | null}
                    onChange={(val) => setSelectedValue(mf.key, val)}
                  />
                ) : (
                  <CheckboxGroup
                    options={mf.options.map((o) => ({ value: o.value, label: o.label }))}
                    value={(mf.selected_value as string[] | undefined) ?? []}
                    onChange={(vals) => setSelectedValue(mf.key, vals)}
                  />
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {card.assumptions.length > 0 && (
        <>
          <SectionTitle>待确认假设 ({card.assumptions.length})</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
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
                  size="sm"
                  variant={a.accepted === true ? 'primary' : 'default'}
                  onClick={() => setAssumptionAccepted(a.key, true)}
                >
                  <CheckOutlined />
                </Button>
                <Button
                  size="sm"
                  variant={a.accepted === false ? 'primary' : 'danger'}
                  onClick={() => setAssumptionAccepted(a.key, false)}
                >
                  <CloseOutlined />
                </Button>
                <span style={{ flex: 1, color: 'var(--ink-2)', fontSize: 12 }}>{a.text}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <div style={{ marginTop: 18, display: 'flex', justifyContent: 'flex-end' }}>
        <Button
          variant="primary"
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
