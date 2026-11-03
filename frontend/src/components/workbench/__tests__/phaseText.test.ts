import { describe, expect, it } from 'vitest'
import {
  COMPOSER_PLACEHOLDERS,
  EXAMPLES,
  agentCopy,
  canvasKicker,
  chatModeForPhase,
  composerPlaceholder,
  settlePhaseFor,
} from '../phaseText'

describe('composerPlaceholder — exact prototype strings', () => {
  it('idle', () =>
    expect(composerPlaceholder('idle')).toBe('输入业务问题，Agent 会先解析需求并请你确认'))
  it('awaiting_missing', () =>
    expect(composerPlaceholder('awaiting_missing')).toBe('也可以直接对话补充，例如：看 2024 全年华东区域'))
  it('awaiting_confirm', () =>
    expect(composerPlaceholder('awaiting_confirm')).toBe('继续补充或修改需求，确认后再生成'))
  it('report_ready', () =>
    expect(composerPlaceholder('report_ready')).toBe('继续调整这份报告，例如：增加华南区域对比'))
  it('error', () => expect(composerPlaceholder('error')).toBe('修改需求或点击重试'))
  it('busy phases share one disabled placeholder', () => {
    expect(composerPlaceholder('parsing')).toBe('Agent 正在处理中…')
    expect(composerPlaceholder('generating')).toBe('Agent 正在处理中…')
    expect(composerPlaceholder('adjusting')).toBe('Agent 正在处理中…')
  })
  it('covers every AnalysisPhase (no undefined)', () => {
    for (const value of Object.values(COMPOSER_PLACEHOLDERS)) {
      expect(typeof value).toBe('string')
      expect(value.length).toBeGreaterThan(0)
    }
  })
})

describe('canvasKicker', () => {
  it('idle → New analysis', () => expect(canvasKicker('idle')).toBe('New analysis'))
  it('report_ready → Report conversation', () =>
    expect(canvasKicker('report_ready')).toBe('Report conversation'))
  it('other phases → Requirement conversation', () => {
    expect(canvasKicker('awaiting_missing')).toBe('Requirement conversation')
    expect(canvasKicker('generating')).toBe('Requirement conversation')
    expect(canvasKicker('error')).toBe('Requirement conversation')
  })
})

describe('chatModeForPhase', () => {
  it('idle → new', () => expect(chatModeForPhase('idle')).toBe('new'))
  // 2026-11-02：出报告后的追问必须回需求层重新解析（否则只是重跑旧卡）
  it('report_ready → supplement（不是 adjust）', () =>
    expect(chatModeForPhase('report_ready')).toBe('supplement'))
  it('awaiting phases → supplement', () => {
    expect(chatModeForPhase('awaiting_missing')).toBe('supplement')
    expect(chatModeForPhase('awaiting_confirm')).toBe('supplement')
  })
})

describe('agentCopy', () => {
  it('parsing carries the no-data-before-confirm promise', () => {
    expect(agentCopy('parsing')).toContain('在你确认前，我不会查询数据或生成报告')
  })
  it('error offers retry without a new session', () => {
    expect(agentCopy('error')).toContain('你可以直接重试，不会创建新会话')
  })
  it('adjusting embeds the adjustment text', () => {
    expect(agentCopy('adjusting', '增加华南区域对比')).toContain('增加华南区域对比')
  })
  it('idle has no bubble', () => expect(agentCopy('idle')).toBe(''))
})

describe('settlePhaseFor — 停止等待 / 断连后回到哪个稳定相位', () => {
  it('没有卡 → idle（不能停在 parsing）', () => expect(settlePhaseFor(null)).toBe('idle'))
  it('卡还有缺失项 → awaiting_missing', () =>
    expect(settlePhaseFor({ missing_fields: [{ key: 'metric' }] })).toBe('awaiting_missing'))
  it('卡已完整 → awaiting_confirm', () =>
    expect(settlePhaseFor({ missing_fields: [] })).toBe('awaiting_confirm'))
  it('undefined missing_fields 视为完整', () =>
    expect(settlePhaseFor({})).toBe('awaiting_confirm'))
})

describe('EXAMPLES', () => {
  it('two prototype examples with exact texts', () => {
    expect(EXAMPLES.map((e) => e.text)).toEqual([
      '帮我分析一下销量',
      '分析 2024 年华东销售趋势和异常',
    ])
  })
})
