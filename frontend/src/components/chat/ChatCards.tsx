import { useState } from 'react'
import { Typography } from 'antd'
import { CheckCircleFilled } from '@ant-design/icons'

import { useSessionStore } from '../../stores/session'
import type { ChatCard, ChatCardType, ConfirmCard, IntentCard, IntentOption, OptionsGroup, PreviewCard } from '../../types/report'

const { Text } = Typography

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
        <Text style={{ fontSize: 12, fontWeight: 600, color: '#475569', display: 'block', marginBottom: 4 }}>
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
              border: `1px solid ${option.selected ? '#1E40AF' : '#E2E8F0'}`,
              borderRadius: 6,
              fontSize: 12,
              cursor: busy ? 'not-allowed' : 'pointer',
              background: option.selected ? '#EFF6FF' : '#FFFFFF',
              color: option.selected ? '#1E40AF' : '#475569',
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
    <div style={{ margin: '8px 0', background: '#EFF6FF', border: '1px solid #DBEAFE', borderRadius: 10, padding: 16 }}>
      <Text strong style={{ fontSize: 13, color: '#1E40AF', display: 'block', marginBottom: 8 }}>
        {data.title}
      </Text>
      {data.items.map((item, index) => (
        <div key={index} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, fontSize: 12, color: '#1E293B' }}>
          <CheckCircleFilled style={{ color: '#059669', fontSize: 12 }} />
          <span>{item.text}</span>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button
          type="button"
          disabled={busy}
          onClick={onModify}
          style={{ padding: '6px 16px', border: '1px solid #E2E8F0', borderRadius: 6, background: '#FFFFFF', color: '#475569', cursor: buttonCursor, fontSize: 12, opacity: busy ? 0.55 : 1 }}
        >
          修改需求
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onStartGenerate}
          style={{ padding: '6px 16px', border: 'none', borderRadius: 6, background: '#1E40AF', color: '#FFFFFF', cursor: buttonCursor, fontSize: 12, opacity: busy ? 0.55 : 1 }}
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
    <div style={{ margin: '8px 0', background: '#FFFFFF', border: '1px solid #E2E8F0', borderRadius: 10, padding: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)' }}>
      <Text strong style={{ fontSize: 13, color: '#1E293B', display: 'block', marginBottom: 10 }}>
        {data.title}
      </Text>
      {data.kpis.length > 0 && (
        <div style={{ display: 'flex', gap: 16, marginBottom: 10 }}>
          {data.kpis.map((kpi, index) => (
            <div key={index} style={{ flex: 1 }}>
              <Text style={{ fontSize: 11, color: '#94A3B8', display: 'block' }}>{kpi.label}</Text>
              <Text strong style={{ fontSize: 18, color: '#1E293B' }}>{kpi.value}</Text>
              {kpi.trend && <Text style={{ fontSize: 11, color: kpi.trend.startsWith('↑') || kpi.trend.startsWith('+') ? '#059669' : '#DC2626' }}>{kpi.trend}</Text>}
            </div>
          ))}
        </div>
      )}
      {data.chartType && (
        <div style={{ height: 120, background: '#F8FAFC', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#CBD5E1', fontSize: 12, marginBottom: 10, border: '1px solid #F1F5F9' }}>
          {data.chartType}
        </div>
      )}
      {data.insight && (
        <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.6, padding: '8px 12px', background: '#F8FAFC', borderRadius: 6, marginBottom: 10 }}>
          {data.insight}
        </div>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={onViewReport}
        style={{ width: '100%', padding: '8px', background: '#EFF6FF', border: '1px solid #DBEAFE', color: '#1E40AF', borderRadius: 6, cursor: busy ? 'not-allowed' : 'pointer', fontSize: 12, fontWeight: 500, opacity: busy ? 0.55 : 1 }}
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
    <div style={{ margin: "12px 0", background: "#FFFFFF", border: "1px solid #DBEAFE", borderRadius: 10, padding: 16 }}>
      <Text style={{ fontSize: 14, fontWeight: 600, color: "#0F172A", display: "block", marginBottom: 12 }}>
        {data.payload.title ?? "我能这样帮你分析 — 选一个继续"}
      </Text>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {data.payload.options.map((opt, idx) => (
          <button
            key={opt.tool + "-" + idx}
            type="button"
            disabled={busy}
            onClick={() => onSelect(opt)}
            style={{ textAlign: "left", padding: "12px 14px", border: "1px solid #E2E8F0", borderRadius: 8, background: "#FFFFFF", cursor: busy ? "not-allowed" : "pointer", fontSize: 13, opacity: busy ? 0.55 : 1 }}
          >
            <div style={{ fontWeight: 500, color: "#0F172A", marginBottom: 4 }}>{opt.label}</div>
            {opt.description && <div style={{ fontSize: 12, color: "#6B7280", lineHeight: 1.5 }}>{opt.description}</div>}
            <div style={{ fontSize: 11, color: "#9CA3AF", marginTop: 6, fontFamily: "monospace" }}>tool: {opt.tool}</div>
          </button>
        ))}
      </div>
      {data.reasoning && <div style={{ marginTop: 12, fontSize: 11, color: "#9CA3AF", fontStyle: "italic" }}>{data.reasoning}</div>}
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
          // Round-2 of the 3-stage flow: send the chosen tool name via
          // `chosen_tool` so the backend plan node can pre-orient the SQL.
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
