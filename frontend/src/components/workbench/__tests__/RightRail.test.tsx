import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RightRail from '../RightRail'
import type { RequirementCard } from '../../../types/requirement'

const REQUIREMENT: RequirementCard = {
  id: 'd1',
  version: 1,
  status: 'missing',
  summary: 'x',
  target_metrics: ['销售额', '毛利额'],
  time_range: '2024年',
  scope: ['华东'],
  dimensions: [],
  analysis_methods: [],
  expected_blocks: [],
  missing_fields: [],
  assumptions: [],
  confidence: 0.8,
  confirmed_at: null,
}

describe('RightRail', () => {
  it('awaiting_missing shows 55% completeness and scope tags', () => {
    render(<RightRail phase="awaiting_missing" requirement={REQUIREMENT} onSuggest={vi.fn()} />)
    expect(screen.getByText('等待补充信息')).toBeTruthy()
    expect(screen.getByText('55%')).toBeTruthy()
    expect(screen.getByText('2024年')).toBeTruthy()
    expect(screen.getByText('华东')).toBeTruthy()
    expect(screen.queryByText('推荐继续分析')).toBeNull()
  })

  it('report_ready renders both suggestions; click prefills via onSuggest', () => {
    const onSuggest = vi.fn()
    render(<RightRail phase="report_ready" requirement={REQUIREMENT} onSuggest={onSuggest} />)
    fireEvent.click(screen.getByText('增加华南区域对比'))
    expect(onSuggest).toHaveBeenCalledWith('增加华南区域对比')
  })

  it('error phase offers the retry narrative', () => {
    render(<RightRail phase="error" requirement={null} onSuggest={vi.fn()} />)
    expect(screen.getByText('任务未完成')).toBeTruthy()
    expect(screen.getByText('错误保留在当前会话，可直接重试。')).toBeTruthy()
  })
})
