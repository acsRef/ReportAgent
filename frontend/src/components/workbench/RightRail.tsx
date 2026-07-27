import { PHASE_INFO, REPORT_SUGGESTIONS } from './phaseInfo'
import type { AnalysisPhase } from '../../types/analysis'
import type { RequirementCard } from '../../types/requirement'

interface Props {
  phase: AnalysisPhase
  requirement: RequirementCard | null
  onSuggest: (text: string) => void
}

/** 分析助手 — phase-driven right rail per the prototype. */
export default function RightRail({ phase, requirement, onSuggest }: Props) {
  const info = PHASE_INFO[phase]
  const showScope =
    requirement !== null && (phase === 'awaiting_missing' || phase === 'awaiting_confirm')

  return (
    <aside
      className="workbench-rail workbench-rail--right"
      style={{ display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}
    >
      <div className="wb-right-head">
        <span className="wb-right-title">分析助手</span>
        <span className="wb-agent-ready">AGENT READY</span>
      </div>
      <div className="wb-right-scroll">
        <div className="wb-side-section">
          <div className="wb-phase-card">
            <div className="wb-phase-name">{info.name}</div>
            <p className="wb-phase-copy">{info.copy}</p>
            <div className="wb-completeness">
              <div className="wb-complete-row">
                <span>完成度</span>
                <span>{info.completeness}%</span>
              </div>
              <div className="wb-complete-track">
                <div className="wb-complete-fill" style={{ width: `${info.completeness}%` }} />
              </div>
            </div>
          </div>
        </div>

        {showScope && requirement && (
          <div className="wb-side-section">
            <div className="wb-side-heading">当前需求范围</div>
            <div className="wb-scope-tags">
              {requirement.time_range && (
                <span className="wb-scope-tag">{requirement.time_range}</span>
              )}
              {requirement.scope.map((item) => (
                <span key={item} className="wb-scope-tag">{item}</span>
              ))}
              {requirement.target_metrics.slice(0, 2).map((metric) => (
                <span key={metric} className="wb-scope-tag">{metric}</span>
              ))}
            </div>
          </div>
        )}

        {phase === 'report_ready' && (
          <div className="wb-side-section">
            <div className="wb-side-heading">推荐继续分析</div>
            <div className="wb-suggestions">
              {REPORT_SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className="wb-suggestion"
                  onClick={() => onSuggest(suggestion)}
                >
                  <span>{suggestion}</span>
                  <span aria-hidden="true">↗</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
