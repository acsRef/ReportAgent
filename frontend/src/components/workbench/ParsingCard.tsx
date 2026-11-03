interface Props {
  /**
   * 停止等待（2026-11-03）：解析轮后台化后「停止」只断前端渲染，后端继续跑完并落库；
   * 此前 parsing 阶段没有任何出口，用户只能刷新页面——而刷新会把当时还在 SSE 响应
   * 任务里的解析一起取消（hand-off P0）。
   */
  onStop?: () => void
}

const STEPS = ['理解业务目标', '匹配指标与维度', '推荐分析方法']

/** parsing-phase card — prototype .parsing-card with thinking dots. */
export default function ParsingCard({ onStop }: Props) {
  return (
    <div className="wb-parsing-card wb-reveal">
      <div className="wb-parsing-top">
        <span className="wb-parsing-title">正在理解分析需求</span>
        <span className="wb-thinking" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      </div>
      <p className="wb-parsing-copy">
        LLM 正在结合可分析字段和分析能力，形成一份可确认的需求草稿。
      </p>
      <div className="wb-parse-steps">
        {STEPS.map((step, index) => (
          <div
            key={step}
            className={index === 0 ? 'wb-parse-step active' : 'wb-parse-step'}
          >
            {step}
          </div>
        ))}
      </div>
      {onStop && (
        <div className="wb-progress-actions">
          <button type="button" className="wb-danger-btn" onClick={onStop}>
            停止等待
          </button>
        </div>
      )}
    </div>
  )
}
