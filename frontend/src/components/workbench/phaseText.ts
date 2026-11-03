/**
 * Phase-driven copy for the workbench UI — strings lifted verbatim from
 * the approved prototype (docs/intelligent-analysis-workbench.html).
 * Pure functions only; unit-tested in __tests__/phaseText.test.ts.
 */
import type { AnalysisPhase } from '../../types/analysis'
import type { ChatRequest } from '../../api/analysisClient'

const BUSY_PLACEHOLDER = 'Agent 正在处理中…'

export const COMPOSER_PLACEHOLDERS: Record<AnalysisPhase, string> = {
  idle: '输入业务问题，Agent 会先解析需求并请你确认',
  parsing: BUSY_PLACEHOLDER,
  awaiting_missing: '也可以直接对话补充，例如：看 2024 全年华东区域',
  awaiting_confirm: '继续补充或修改需求，确认后再生成',
  generating: BUSY_PLACEHOLDER,
  adjusting: BUSY_PLACEHOLDER,
  report_ready: '继续调整这份报告，例如：增加华南区域对比',
  error: '修改需求或点击重试',
}

export function composerPlaceholder(phase: AnalysisPhase): string {
  return COMPOSER_PLACEHOLDERS[phase] ?? BUSY_PLACEHOLDER
}

export function canvasKicker(phase: AnalysisPhase): string {
  if (phase === 'idle') return 'New analysis'
  if (phase === 'report_ready') return 'Report conversation'
  return 'Requirement conversation'
}

/** Agent chat-bubble copy per phase (markdown; rendered via react-markdown). */
export function agentCopy(phase: AnalysisPhase, adjustText?: string): string {
  switch (phase) {
    case 'parsing':
      return '我会先拆解你的问题，确认分析目标和范围。**在你确认前，我不会查询数据或生成报告。**'
    case 'awaiting_missing':
      return '我已经完成初步拆解，但还缺少关键信息。请确认下面的需求草稿；**这些缺失项由后端分析结果返回**。'
    case 'awaiting_confirm':
      return '需求已经完整。我建议按下面的方法组织分析，请确认后再开始查询和生成报告。'
    case 'generating':
      return '需求已确认。现在开始查询数据并生成报告，你可以在右侧查看阶段进度。'
    case 'adjusting':
      return `收到调整要求：**${adjustText ?? '重新生成'}**。我会保留当前报告，并生成新版本。`
    case 'report_ready':
      return '报告已生成并保留在当前会话。你可以继续对话调整，新的结果会保存为下一版本。'
    case 'error':
      return '报告生成中断，但已确认需求和对话都已保留。你可以直接重试，不会创建新会话。'
    default:
      return ''
  }
}

export interface WorkbenchExample {
  kicker: string
  text: string
}

export const EXAMPLES: WorkbenchExample[] = [
  { kicker: '宽泛问题 · 体验补充流程', text: '帮我分析一下销量' },
  { kicker: '明确问题 · 体验直接确认', text: '分析 2024 年华东销售趋势和异常' },
]

/** Which /chat mode a composer submit should use in each phase. */
export function chatModeForPhase(phase: AnalysisPhase): NonNullable<ChatRequest['mode']> {
  if (phase === 'idle') return 'new'
  // 2026-11-02：report_ready 之后的追问必须回到需求层（supplement：继承上一轮卡片
  // 重新解析），此前后续用 adjust 直接重跑旧卡 —— 用户说「详细讲一下七月份」
  // 得到的是同一张 2024 全年卡的第二份报告，SQL 一模一样。
  if (phase === 'report_ready') return 'supplement'
  return 'supplement'
}

/**
 * 停止等待 / 断连后应回到的稳定相位（2026-11-03）。
 *
 * 解析轮（parsing）后台化后，「停止」只断前端渲染、后端继续跑并落库；前端不能
 * 停在 parsing（busy 会把发送框永久禁掉），也不能凭空宣布成功——按最后一张卡
 * 的缺失项回到它本来的相位；一张卡都没有就回 idle。
 */
export function settlePhaseFor(card: { missing_fields?: unknown[] } | null): AnalysisPhase {
  if (!card) return 'idle'
  return (card.missing_fields?.length ?? 0) > 0 ? 'awaiting_missing' : 'awaiting_confirm'
}
