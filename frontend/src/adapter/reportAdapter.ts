import type { ReportBlock, ReportResponse } from '../types/report'

export function adaptReport(resp: ReportResponse): ReportBlock[] {
  const blocks: ReportBlock[] = []
  const answer = resp.answer
  if (!answer) return blocks

  if (answer.insight) {
    blocks.push({
      id: 'insight',
      type: 'insight',
      title: 'AI 洞察',
      data: { content: answer.insight },
    })
  }

  if (answer.text) {
    blocks.push({
      id: 'summary',
      type: 'markdown',
      title: '摘要',
      data: { content: answer.text },
    })
  }

  if (answer.table && answer.table.columns.length > 0) {
    // We always emit the table block — even when rows=[] — so the
    // front-end can render the "未找到匹配记录" band for the legitimate
    // zero-match case (execution_status=EMPTY). The old guard
    // (`rows.length > 0`) silently dropped the table for EMPTY runs,
    // making them look indistinguishable from FAILED.
    blocks.push({
      id: 'table',
      type: 'table',
      title: '数据明细',
      data: {
        ...(answer.table as unknown as Record<string, unknown>),
        empty: answer.table.rows.length === 0,
      },
    })
  }

  if (answer.chart) {
    const chart = answer.chart as Record<string, unknown>
    const chartType = String(chart.type || '')
    const rawConfig = (chart.config as Record<string, unknown>) || chart
    const dimensions = rawConfig.dimensions as Record<string, string> | undefined

    const chartData = (rawConfig.data as Record<string, unknown>[]) || (chart.data as Record<string, unknown>[]) || []

    if (chartData.length) {
      const normalizedConfig: Record<string, unknown> = { ...rawConfig }

      if (dimensions) {
        normalizedConfig.xField = dimensions.category || dimensions.x || ''
        normalizedConfig.yField = dimensions.value || dimensions.y || ''
      }

      blocks.push({
        id: 'chart',
        type: 'chart',
        title: chart.title as string || '可视化',
        data: { type: chartType, config: normalizedConfig, data: chartData },
      })
    }
  }

  return blocks
}
