/** 4 table rendering modes matching prototype.html design */
interface Props {
  columns: Array<{ key: string; title: string }>
  rows: Record<string, unknown>[]
  hoverStyle: (e: React.MouseEvent<HTMLElement>, on: boolean) => void
  cellBg: (idx: number) => string
}

const TH_STYLE: React.CSSProperties = {
  padding: '8px 12px', background: '#F8FAFC', borderBottom: '2px solid #E2E8F0',
  textAlign: 'left', fontWeight: 600, color: '#64748B', fontSize: 12, whiteSpace: 'nowrap',
}
const TD_STYLE: React.CSSProperties = {
  padding: '8px 12px', borderBottom: '1px solid #F1F5F9', fontSize: 13, color: '#1E293B',
}

export function renderFlatTable({ columns, rows, hoverStyle, cellBg }: Props) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>
          <th style={{ ...TH_STYLE, width: 40 }}>#</th>
          {columns.map((c) => <th key={c.key} style={TH_STYLE}>{c.title || c.key}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 50).map((row, idx) => (
          <tr
            key={idx}
            style={{ transition: 'background 0.15s', background: cellBg(idx) }}
            onMouseEnter={(e) => hoverStyle(e, true)}
            onMouseLeave={(e) => hoverStyle(e, false)}
          >
            <td style={{ ...TD_STYLE, color: '#94A3B8', width: 40 }}>{idx + 1}</td>
            {columns.map((c) => (
              <td key={c.key} style={TD_STYLE}>{String(row[c.key] ?? '')}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function renderTreeTable({ columns, rows, hoverStyle, cellBg }: Props) {
  // Group rows by first column for rowspan
  const col0 = columns[0]?.key || ''
  const spanMap = new Map<string, number>()
  let prevVal = ''
  for (const row of rows) {
    const val = String(row[col0] || '')
    if (val !== prevVal) {
      spanMap.set(val, 0)
      prevVal = val
    }
    spanMap.set(val, (spanMap.get(val) || 0) + 1)
  }

  let shownGroups = new Set<string>()
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>
          {columns.map((c) => <th key={c.key} style={TH_STYLE}>{c.title || c.key}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 50).map((row, idx) => {
          const groupVal = String(row[col0] || '')
          const isFirst = !shownGroups.has(groupVal)
          if (isFirst) shownGroups.add(groupVal)
          const colSpan = spanMap.get(groupVal) || 1
          return (
            <tr
              key={idx}
              style={{ transition: 'background 0.15s', background: cellBg(idx) }}
              onMouseEnter={(e) => hoverStyle(e, true)}
              onMouseLeave={(e) => hoverStyle(e, false)}
            >
              {isFirst ? (
                <td rowSpan={colSpan} style={{ ...TD_STYLE, fontWeight: 600, color: '#1E40AF', background: '#F5F8FF', fontSize: 13, padding: '10px 12px' }}>
                  {groupVal}
                </td>
              ) : null}
              {columns.slice(1).map((c) => (
                <td key={c.key} style={TD_STYLE}>{String(row[c.key] ?? '')}</td>
              ))}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function renderIndentTable({ columns, rows, hoverStyle, cellBg }: Props) {
  const depthStyle = (depth: number): React.CSSProperties => ({
    ...TD_STYLE,
    paddingLeft: `${16 + depth * 20}px`,
    fontWeight: depth === 1 ? 600 : 400,
    color: depth === 1 ? '#1E40AF' : depth === 3 ? '#666' : '#1E293B',
    fontSize: depth === 1 ? 13 : depth === 3 ? 12 : 13,
  })

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>
          <th style={TH_STYLE}>层级</th>
          {columns.map((c) => <th key={c.key} style={{ ...TH_STYLE, textAlign: 'right' }}>{c.title || c.key}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 50).map((row, idx) => {
          const depth = Number(row.depth || row.level || 0) || 1
          return (
            <tr
              key={idx}
              style={{ transition: 'background 0.15s', background: cellBg(idx) }}
              onMouseEnter={(e) => hoverStyle(e, true)}
              onMouseLeave={(e) => hoverStyle(e, false)}
            >
              <td style={depthStyle(depth)}>{row.label as string || String(row[columns[0]?.key] ?? '')}</td>
              {columns.slice(1).map((c) => (
                <td key={c.key} style={{ ...TD_STYLE, textAlign: 'right' }}>{String(row[c.key] ?? '')}</td>
              ))}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function renderCrossTable({ columns, rows, hoverStyle, cellBg }: Props) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
        <tr>
          {columns.map((c, i) => (
            <th key={c.key} style={{
              ...TH_STYLE, textAlign: 'center', border: '1px solid #E2E8F0',
              background: i === 0 ? '#F1F5F9' : '#F8FAFC', fontWeight: 600,
            }}>
              {c.title || c.key}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, idx) => (
          <tr
            key={idx}
            style={{ transition: 'background 0.15s', background: cellBg(idx) }}
            onMouseEnter={(e) => hoverStyle(e, true)}
            onMouseLeave={(e) => hoverStyle(e, false)}
          >
            {columns.map((c, ci) => {
              const val = String(row[c.key] ?? '')
              const isHeader = ci === 0
              const isNumber = !isNaN(Number(val)) && val !== '' && !isHeader
              return (
                <td key={c.key} style={{
                  padding: '8px 10px', border: '1px solid #E2E8F0', fontSize: 12,
                  color: isHeader ? '#1E40AF' : '#1E293B',
                  fontWeight: isHeader ? 500 : 400,
                  background: isHeader ? '#F8FAFC' : 'transparent',
                  textAlign: isNumber || ci > 0 ? 'right' : 'left',
                }}>
                  {val}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}