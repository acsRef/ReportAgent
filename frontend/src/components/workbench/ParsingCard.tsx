const STEPS = ['理解业务目标', '匹配指标与维度', '推荐分析方法']

/** parsing-phase card — prototype .parsing-card with thinking dots. */
export default function ParsingCard() {
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
    </div>
  )
}
