import { EXAMPLES } from './phaseText'

interface Props {
  /** Clicking an example submits it immediately. */
  onPick: (text: string) => void
}

/** Idle-phase empty state — prototype .empty-state with two examples. */
export default function WorkbenchEmpty({ onPick }: Props) {
  return (
    <div className="wb-empty-state">
      <div className="wb-empty-inner wb-reveal">
        <div className="wb-empty-mark">
          <svg
            width="34"
            height="34"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M4 20V10" />
            <path d="M10 20V4" />
            <path d="M16 20v-7" />
            <path d="M21 20H3" />
          </svg>
        </div>
        <h1 className="wb-empty-title">先把问题说清楚，再生成可信报告</h1>
        <p className="wb-empty-copy">
          输入业务问题后，Agent 会先拆解目标、指标、范围和分析方式。你确认最终需求后，系统才会查询数据并生成报告。
        </p>
        <div className="wb-example-grid">
          {EXAMPLES.map((example) => (
            <button
              key={example.text}
              type="button"
              className="wb-example"
              onClick={() => onPick(example.text)}
            >
              <span className="wb-example-type">{example.kicker}</span>
              <span className="wb-example-text" style={{ display: 'block' }}>
                {example.text}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
