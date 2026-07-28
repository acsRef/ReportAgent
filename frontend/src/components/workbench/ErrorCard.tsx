interface Props {
  message?: string | null
  /** Failure category from the backend. Drives the card title so each
   *  kind (timeout / connection / permission / etc.) shows distinct,
   *  actionable copy. Optional — legacy callers fall back to a generic
   *  title. */
  kind?: 'timeout' | 'syntax' | 'object' | 'connection' | 'permission' | 'other' | null
  /** SQL the agent actually tried, clipped to ≤200 chars by the
   *  backend. Shown in a collapsible disclosure so it's there when
   *  the user needs to debug without spamming the toast / card. */
  sql?: string | null
  onRetry: () => void
  retrying?: boolean
}

const KIND_TITLE: Record<NonNullable<Props['kind']>, string> = {
  timeout:    '查询超时',
  connection: '数据库连接失败',
  permission: '权限不足',
  syntax:     'SQL 语法错误',
  object:     '查询对象不存在',
  other:      '查询执行失败',
}

/** error-phase card — red .progress-card with 重试当前任务.

 *  Title is driven by `kind` so the user sees the specific failure
 *  (vs the generic "执行分析时发生错误"). The full `message` (which
 *  includes the tried SQL appended by the backend) renders in the
 *  detail row; if `sql` is present, a collapsible disclosure lets the
 *  user copy just the SQL.
 */
export default function ErrorCard({ message, kind, sql, onRetry, retrying }: Props) {
  const title = kind ? KIND_TITLE[kind] : '执行分析时发生错误'
  return (
    <div className="wb-progress-card failed wb-reveal">
      <div className="wb-progress-top">
        <span className="wb-progress-title">{title}</span>
        <span className="wb-progress-pct">FAILED</span>
      </div>
      <div className="wb-progress-detail">{message ?? '查询未能返回数据。'}</div>
      {sql && (
        <details className="wb-progress-sql">
          <summary>查看尝试的 SQL</summary>
          <pre><code>{sql}</code></pre>
        </details>
      )}
      <div className="wb-progress-actions">
        <button type="button" className="wb-primary" onClick={onRetry} disabled={retrying}>
          重试当前任务
        </button>
      </div>
    </div>
  )
}
