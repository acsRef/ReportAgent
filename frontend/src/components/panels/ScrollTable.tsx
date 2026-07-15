import type { ScrollTableProps, TableColumn } from '../../types/panel';
import './ScrollTable.css';

function renderValue(val: unknown): string {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'number') return val.toLocaleString();
  return String(val);
}

function getRankClass(index: number): string {
  if (index === 0) return 'rank-1';
  if (index === 1) return 'rank-2';
  if (index === 2) return 'rank-3';
  return 'rank-other';
}

export default function ScrollTable(props: ScrollTableProps) {
  const { title, columns, data, speed = 8000 } = props;

  if (!data || data.length === 0) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--color-text-muted)' }}>暂无数据</div>;
  }

  // Duplicate data for seamless scroll
  const displayData = [...data, ...data];

  return (
    <div className="scroll-table-panel" style={{ '--scroll-speed': `${speed}ms` } as React.CSSProperties}>
      {title && <div className="scroll-table-title">{title}</div>}
      <div className="scroll-table-wrapper">
        <div className="scroll-table-body">
          <table className="scroll-table">
            <thead>
              <tr>
                <th style={{ width: 36 }}>#</th>
                {(columns as TableColumn[]).map((col) => (
                  <th key={col.key} style={{ textAlign: col.align || 'left' }}>{col.title}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayData.map((row, i) => (
                <tr key={i}>
                  <td>
                    <span className={`rank ${getRankClass(i % data.length)}`}>
                      {(i % data.length) + 1}
                    </span>
                  </td>
                  {(columns as TableColumn[]).map((col) => (
                    <td key={col.key} style={{ textAlign: col.align || 'left' }}>
                      {renderValue(row[col.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}