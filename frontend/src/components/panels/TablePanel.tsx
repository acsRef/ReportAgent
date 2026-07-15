import { useState, useMemo } from 'react';
import type { TableProps, TableColumn } from '../../types/panel';
import './TablePanel.css';

function renderVal(val: unknown): string {
  if (val == null) return '—';
  if (typeof val === 'number') return val.toLocaleString();
  return String(val);
}

export default function TablePanel(props: TableProps) {
  const { title, columns, data, pageSize = 10 } = props;
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const sorted = useMemo(() => {
    if (!sortKey) return data;
    return [...data].sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [data, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const paged = sorted.slice(page * pageSize, (page + 1) * pageSize);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
    setPage(0);
  };

  if (!data || data.length === 0) {
    return <div className="table-panel-empty">暂无表格数据</div>;
  }

  return (
    <div className="table-panel">
      {title && <div className="table-panel-title">{title}</div>}
      <div className="table-panel-body">
        <table>
          <thead>
            <tr>
              {(columns as TableColumn[]).map((col) => (
                <th
                  key={col.key}
                  className={col.sortable ? 'sortable' : ''}
                  onClick={() => col.sortable && handleSort(col.key)}
                  style={{ textAlign: col.align || 'left' }}
                >
                  {col.title}
                  {col.sortable && (
                    <span className={`sort-icon ${sortKey === col.key ? 'active' : ''}`}>
                      {sortKey === col.key ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.map((row, i) => (
              <tr key={i}>
                {(columns as TableColumn[]).map((col) => (
                  <td key={col.key} style={{ textAlign: col.align || 'left' }}>
                    {renderVal(row[col.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > pageSize && (
        <div className="table-pagination">
          <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>上一页</button>
          <span>{page + 1} / {totalPages}</span>
          <button disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>下一页</button>
        </div>
      )}
    </div>
  );
}
