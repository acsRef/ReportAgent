import { useState } from 'react'
import { IconCheckCircle } from '../ui/Icons'
import { Text } from '../atelier/Typography'
import { useSessionStore } from '../../stores/session'
import type { ChatCard, ChatCardType, ConfirmCard, IntentCard, IntentOption, OptionsGroup, PreviewCard } from '../../types/report'

interface Props {
  type: ChatCardType
  data: ChatCard
  cardId: string
  onModify: () => void
}

interface CardActions {
  busy: boolean
  onModify: () => void
  onStartGenerate: () => void
  onViewReport: () => void
}

function OptionsGroupCard({ data, busy, onSelect }: {
  data: OptionsGroup
  busy: boolean
  onSelect: (value: string) => void
}) {
  return (
    <div style={{ margin: '4px 0' }}>
      {data.label && (
        <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', display: 'block', marginBottom: 4 }}>
          {data.label}
        </Text>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {data.options.map((option) => (
          <button
            key={option.value}
            type="button"
            disabled={busy}
            onClick={() => onSelect(option.value)}
            style={{
              padding: '4px 12px',
              border: `1px solid ${option.selected ? 'var(--teal-deep)' : 'var(--line)'}`,
              borderRadius: 6,
              fontSize: 12,
              cursor: busy ? 'not-allowed' : 'pointer',
              background: option.selected ? 'var(--teal-soft)' : 'var(--paper)',
              color: option.selected ? 'var(--teal-deep)' : 'var(--ink-2)',
              fontWeight: option.selected ? 500 : 400,
              opacity: busy ? 0.55 : 1,
              transition: 'all 0.15s',
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function ConfirmCardView({ data, busy, onModify, onStartGenerate }: {
  data: ConfirmCard
  busy: boolean
  onModify: () => void
  onStartGenerate: () => void
}) {
  const buttonCursor = busy ? 'not-allowed' : 'pointer'
  return (
    <div style={{ margin: '8px 0', background: 'var(--teal-soft)', border: '1px solid var(--teal-pale)', borderRadius: 10, padding: 16 }}>
      <Text strong style={{ fontSize: 13, color: 'var(--teal-deep)', display: 'block', marginBottom: 8 }}>
        {data.title}
      </Text>
      {data.items.map((item, index) => (
        <div key={index} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, fontSize: 12, color: 'var(--ink)' }}>
          <IconCheckCircle style={{ color: '#059669', width: 12, height: 12 }} />
          <span>{item.text}</span>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button
          type="button"
          disabled={busy}
          onClick={onModify}
          style={{ padding: '6px 16px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--paper)', color: 'var(--ink-2)', cursor: buttonCursor, fontSize: 12, opacity: busy ? 0.55 : 1 }}
        >
          修改需求
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onStartGenerate}
          style={{ padding: '6px 16px', border: 'none', borderRadius: 6, background: 'var(--teal-deep)', color: 'var(--paper)', cursor: buttonCursor, fontSize: 12, opacity: busy ? 0.55 : 1 }}
        >
          开始生成
        </button>
      </div>
    </div>
  )
}

function PreviewCardView({ data, busy, onViewReport }: {
  data: PreviewCard
  busy: boolean
  onViewReport: () => void
}) {
  return (
    <div style={{ margin: '8px 0', background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 10, padding: 16, boxShadow: 'var(--shadow-card)' }}>
      <Text strong style={{ fontSize: 13, color: 'var(--ink)', display: 'block', marginBottom: 10 }}>
        {data.title}
      </Text>
      {data.kpis.length > 0 && (
        <div style={{ display: 'flex', gap: 16, marginBottom: 10 }}>
          {data.kpis.map((kpi, index) => (
            <div key={index} style={{ flex: 1 }}>
              <Text style={{ fontSize: 11, color: 'var(--muted)', display: 'block' }}>{kpi.label}</Text>
              <Text strong style={{ fontSize: 18, color: 'var(--ink)' }}>{kpi.value}</Text>
              {kpi.trend && <Text style={{ fontSize: 11, color: kpi.trend.startsWith('↑') || kpi.trend.startsWith('+') ? '#059669' : '#DC2626' }}>{kpi.trend}</Text>}
            </div>
          ))}
        </div>
      )}
      {data.chartType && (
        <div style={{ height: 120, background: 'var(--canvas)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--faint)', fontSize: 12, marginBottom: 10, border: '1px solid var(--line)' }}>
          {data.chartType}
        </div>
      )}
      {data.insight && (
        <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.6, padding: '8px 12px', background: 'var(--canvas)', borderRadius: 6, marginBottom: 10 }}>
          {data.insight}
        </div>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={onViewReport}
        style={{ width: '100%', padding: '8px', background: 'var(--teal-soft)', border: '1px solid var(--teal-pale)', color: 'var(--teal-deep)', borderRadius: 6, cursor: busy ? 'not-allowed' : 'pointer', fontSize: 12, fontWeight: 500, opacity: busy ? 0.55 : 1 }}
      >
        查看完整报告 →
      </button>
    </div>
  )
}

function IntentCardView({ data, busy, onSelect }: {
  data: IntentCard
  busy: boolean
  onSelect: (option: IntentOption) => void
}) {
  return (
    <div style={{ margin: "12px 0", background: "var(--paper)", border: "1px solid var(--teal-pale)", borderRadius: 10, padding: 16 }}>
      <Text style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)", display: "block", marginBottom: 12 }}>
        {data.payload.title ?? "我能这样帮你分析 — 选一个继续"}
      </Text>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {data.payload.options.map((opt, idx) => (
          <button
            key={opt.tool + "-" + idx}
            type="button"
            disabled={busy}
            onClick={() => onSelect(opt)}
            style={{ textAlign: "left", padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--paper)", cursor: busy ? "not-allowed" : "pointer", fontSize: 13, opacity: busy ? 0.55 : 1 }}
          >
            <div style={{ fontWeight: 500, color: "var(--ink)", marginBottom: 4 }}>{opt.label}</div>
            {opt.description && <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>{opt.description}</div>}
            <div style={{ fontSize: 11, color: "var(--faint)", marginTop: 6, fontFamily: "monospace" }}>tool: {opt.tool}</div>
          </button>
        ))}
      </div>
      {data.reasoning && <div style={{ marginTop: 12, fontSize: 11, color: "var(--faint)", fontStyle: "italic" }}>{data.reasoning}</div>}
    </div>
  )
}

export default function ChatCards({ type, data, cardId, onModify }: Props) {
  const { busy, pendingReportBlocks, sendMessage, sessionId } = useSessionStore()
  const [options, setOptions] = useState(data.type === 'options_group' ? data.options : [])

  const actions: CardActions = {
    busy,
    onModify,
    onStartGenerate: () => {
      const synthesized = 'synthesized_query' in data ? data.synthesized_query : undefined
      if (synthesized) void sendMessage(synthesized, { refineOf: cardId })
    },
    onViewReport: () => {
      const blocks = encodeURIComponent(JSON.stringify(pendingReportBlocks ?? []))
      window.open(`/report/${encodeURIComponent(sessionId)}?blocks=${blocks}`, '_blank', 'noopener,noreferrer')
    },
  }

  if (type === 'options_group' && data.type === 'options_group') {
    const card = { ...data, options }
    return (
      <OptionsGroupCard
        data={card}
        busy={busy}
        onSelect={(value) => {
          if (busy) return
          setOptions((current) => current.map((option) => ({
            ...option,
            selected: option.value === value ? !option.selected : data.multi ? option.selected : false,
          })))
        }}
      />
    )
  }
  if (type === 'intent_card' && data.type === 'intent_card') {
    return (
      <IntentCardView
        data={data}
        busy={busy}
        onSelect={(opt) => {
          if (busy) return
          void sendMessage('继续', { chosenTool: opt.tool })
        }}
      />
    )
  }
  if (type === 'confirm_card' && data.type === 'confirm_card') {
    return <ConfirmCardView data={data} {...actions} />
  }
  if (type === 'preview_card' && data.type === 'preview_card') {
    return <PreviewCardView data={data} busy={actions.busy} onViewReport={actions.onViewReport} />
  }
  return null
}
