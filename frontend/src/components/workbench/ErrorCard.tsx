interface Props {
  message?: string | null
  onRetry: () => void
  retrying?: boolean
}

/** error-phase card — red .progress-card with 重试当前任务. */
export default function ErrorCard({ message, onRetry, retrying }: Props) {
  return (
    <div className="wb-progress-card failed wb-reveal">
      <div className="wb-progress-top">
        <span className="wb-progress-title">执行分析时发生错误</span>
        <span className="wb-progress-pct">FAILED</span>
      </div>
      <div className="wb-progress-detail">{message ?? '查询未能返回数据。'}</div>
      <div className="wb-progress-actions">
        <button type="button" className="wb-primary" onClick={onRetry} disabled={retrying}>
          重试当前任务
        </button>
      </div>
    </div>
  )
}
