/**
 * Right-rail phase card content — the 8 exact prototype triples
 * (name / copy / completeness) plus report_ready suggestions.
 */
import type { AnalysisPhase } from '../../types/analysis'

export interface PhaseInfo {
  name: string
  copy: string
  completeness: number
}

export const PHASE_INFO: Record<AnalysisPhase, PhaseInfo> = {
  idle: {
    name: '开始新分析',
    copy: '输入业务问题后，Agent 会先拆解需求，不会直接查询数据。',
    completeness: 0,
  },
  parsing: {
    name: '正在解析需求',
    copy: '正在理解目标、范围和建议分析方式。',
    completeness: 28,
  },
  awaiting_missing: {
    name: '等待补充信息',
    copy: '后端判断当前需求还有关键信息未确定。',
    completeness: 55,
  },
  awaiting_confirm: {
    name: '等待最终确认',
    copy: '需求已完整，确认后才会查询数据并生成报告。',
    completeness: 100,
  },
  generating: {
    name: '正在生成报告',
    copy: '需求已锁定，Agent 正在准备数据和组织报告。',
    completeness: 100,
  },
  adjusting: {
    name: '正在调整报告',
    copy: '保留当前版本，并生成一个新的报告版本。',
    completeness: 100,
  },
  report_ready: {
    name: '报告已完成',
    copy: '你可以继续对话调整，新的结果会保存为下一版本。',
    completeness: 100,
  },
  error: {
    name: '任务未完成',
    copy: '错误保留在当前会话，可直接重试。',
    completeness: 100,
  },
}

export const REPORT_SUGGESTIONS = ['增加华南区域对比', '深入解释异常月份']
